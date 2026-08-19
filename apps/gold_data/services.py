"""
Real-data equivalents of apps.dashboard.services' dummy get_kpis()/
get_chart_data()/get_filter_options() — same output shapes (KPI card list,
Chart.js payload, filter dropdown options), sourced from the 'gold'
Postgres database instead of hardcoded values.

get_kpis()/_streams_over_time_chart() used to read a pre-aggregated global
dashboard_summary/monthly_trends table (one row per month, whole platform).
That table no longer exists in the current Gold source — monthly_trends is
now country x month grain — so both are rebuilt here as cross-country sums
grouped by (year, month) when no country filter is applied. This is an
approximation: summing active_songs/active_artists across countries
double-counts any song/artist active in more than one country that month
(the old upstream table did this aggregation once, correctly, before
landing in Gold — this recomputes it client-side from country-grain rows,
which is the best available signal now).

year/country filter params (added for the dashboard's filter bar): every
function defaults to None (today's global/latest-period behavior,
unchanged) — passing a real year/country scopes the query without
touching the no-filter code path at all.
"""
from django.db.models import Count, Sum

from .models import ArtistPerformance, CountryPerformance, MonthlyTrends


def get_filter_options():
    """Dropdown options for the dashboard's filter bar — real years and
    country names present in the data, each list led by an "All ..."
    sentinel (empty string value, meaning "no filter")."""
    years = list(
        CountryPerformance.objects.using('gold')
        .order_by('-year').values_list('year', flat=True).distinct()
    )
    countries = list(
        CountryPerformance.objects.using('gold')
        .order_by('country_name').values_list('country_name', flat=True).distinct()
    )
    return {
        'years': [{'value': '', 'label': 'All Years'}] + [
            {'value': str(y), 'label': str(y)} for y in years
        ],
        'countries': [{'value': '', 'label': 'All Countries'}] + [
            {'value': c, 'label': c} for c in countries
        ],
    }


def _latest_periods(country=None, year=None, n=2):
    """Most recent (year, month) pairs present in monthly_trends (or
    country_performance when a single country is filtered, since
    monthly_trends is unfiltered-only cross-country grain), newest first.
    `year`, when given, filters directly in the query — NOT "fetch the
    latest N overall, then discard non-matching years", which would miss
    an older selected year entirely once 10 years of monthly data (120+
    periods) exist and the true latest N never reaches back that far."""
    if country:
        qs = CountryPerformance.objects.using('gold').filter(country_name=country)
    else:
        qs = MonthlyTrends.objects.using('gold')
    if year:
        qs = qs.filter(year=year)
    return list(qs.values('year', 'month').distinct().order_by('-year', '-month')[:n])


def _period_aggregate(year, month, country=None):
    if country:
        qs = CountryPerformance.objects.using('gold').filter(year=year, month=month, country_name=country)
    else:
        qs = MonthlyTrends.objects.using('gold').filter(year=year, month=month)
    agg = qs.aggregate(
        total_streams=Sum('total_streams'),
        active_artists=Sum('active_artists'),
        active_songs=Sum('active_songs'),
        hit_songs=Sum('hit_songs'),
        countries_covered=Count('country_name', distinct=True),
    )
    agg['catalog_hit_rate'] = (
        agg['hit_songs'] / agg['active_songs'] * 100
        if agg['active_songs'] else None
    )
    return agg


def get_kpis(year=None, country=None):
    """KPI cards from the most recent two (year, month) periods — summed
    across countries by default, or scoped to a single country when
    `country` is given. `year` restricts which periods count as "latest"
    (the most recent month within that year, compared to the month
    before it) instead of the true latest overall."""
    periods = _latest_periods(country=country, year=year, n=2)
    if not periods:
        return []
    latest_period = periods[0]
    latest = _period_aggregate(latest_period['year'], latest_period['month'], country=country)
    prev = (
        _period_aggregate(periods[1]['year'], periods[1]['month'], country=country)
        if len(periods) > 1 else None
    )
    year_month = f"{latest_period['year']}-{latest_period['month']:02d}"

    def delta(curr, prev_val):
        if not curr or not prev_val:
            return None
        pct = (curr - prev_val) / prev_val * 100
        return f"{pct:+.1f}%", 'up' if pct >= 0 else 'down'

    streams_delta = delta(latest['total_streams'], prev['total_streams'] if prev else None)
    artists_delta = delta(latest['active_artists'], prev['active_artists'] if prev else None)

    return [
        {
            'id': 'total-streams',
            'label': f'Total Streams ({year_month})',
            'value': f"{latest['total_streams'] / 1e9:.2f}B" if latest['total_streams'] else '—',
            'delta': streams_delta[0] if streams_delta else '—',
            'trend': streams_delta[1] if streams_delta else 'up',
            'icon': 'bi-soundwave',
        },
        {
            'id': 'active-artists',
            'label': 'Active Artists',
            'value': f"{latest['active_artists']:,}" if latest['active_artists'] else '—',
            'delta': artists_delta[0] if artists_delta else '—',
            'trend': artists_delta[1] if artists_delta else 'up',
            'icon': 'bi-mic',
        },
        {
            'id': 'countries-covered',
            'label': 'Countries Covered',
            'value': '1' if country else str(latest['countries_covered']),
            'delta': '—',
            'trend': 'up',
            'icon': 'bi-globe',
        },
        {
            'id': 'catalog-hit-rate',
            'label': 'Catalog Hit Rate',
            'value': f"{latest['catalog_hit_rate']:.1f}%" if latest['catalog_hit_rate'] is not None else '—',
            'delta': '—',
            'trend': 'up',
            'icon': 'bi-graph-up-arrow',
        },
    ]


def _streams_over_time_chart(country=None):
    if country:
        qs = CountryPerformance.objects.using('gold').filter(country_name=country)
    else:
        qs = MonthlyTrends.objects.using('gold')
    rows = qs.values('year', 'month').annotate(total=Sum('total_streams')).order_by('year', 'month')
    return {
        'type': 'line',
        'labels': [f"{r['year']}-{r['month']:02d}" for r in rows],
        'datasets': [{
            'label': f'Total Streams{f" ({country})" if country else ""}',
            'data': [r['total'] for r in rows],
        }],
    }


def _top_countries_chart(year=None):
    """Sum streams per country within a year — country_performance is
    monthly grain, so a naive top-N query returns the same country
    multiple times (once per month) instead of one bar per country.
    Deliberately does NOT accept a `country` filter — comparing one
    country to itself is meaningless, so this chart always shows the
    global top-8 for the selected (or latest) year regardless of any
    country filter applied elsewhere on the dashboard."""
    target_year = year or (
        CountryPerformance.objects.using('gold').order_by('-year').values_list('year', flat=True).first()
    )
    rows = (
        CountryPerformance.objects.using('gold')
        .filter(year=target_year)
        .values('country_name')
        .annotate(yearly_streams=Sum('total_streams'))
        .order_by('-yearly_streams')[:8]
    )
    return {
        'type': 'bar',
        'labels': [r['country_name'] for r in rows],
        'datasets': [{
            'label': f'Total Streams ({target_year})',
            'data': [r['yearly_streams'] for r in rows],
        }],
    }


def _top_artists_chart(year=None, country=None):
    """Top 8 artists by total_streams — same one-bar-per-entity pattern as
    _top_countries_chart(), but DOES accept a country filter (unlike that
    chart) since "top artists in India" is a meaningful, common question."""
    qs = ArtistPerformance.objects.using('gold')
    if year:
        qs = qs.filter(year=year)
    if country:
        qs = qs.filter(country_name=country)
    rows = (
        qs.values('artist_name')
        .annotate(streams=Sum('total_streams'))
        .exclude(artist_name__isnull=True)
        .order_by('-streams')[:8]
    )
    year_note = f" ({year})" if year else ""
    return {
        'type': 'bar',
        'labels': [r['artist_name'] for r in rows],
        'datasets': [{
            'label': f'Total Streams{year_note}',
            'data': [r['streams'] for r in rows],
        }],
    }


def _hit_rate_trend_chart(country=None):
    """hit_songs / active_songs, as a percentage, per period — a catalog-
    quality signal alongside the raw-volume streams-over-time chart.
    Sourced from country_performance when a country is filtered (it's the
    only table with hit_songs at that grain), monthly_trends otherwise."""
    if country:
        qs = CountryPerformance.objects.using('gold').filter(country_name=country)
    else:
        qs = MonthlyTrends.objects.using('gold')
    rows = list(
        qs.values('year', 'month')
        .annotate(hit_songs=Sum('hit_songs'), active_songs=Sum('active_songs'))
        .order_by('year', 'month')
    )
    return {
        'type': 'line',
        'labels': [f"{r['year']}-{r['month']:02d}" for r in rows],
        'datasets': [{
            'label': f'Hit Rate %{f" ({country})" if country else ""}',
            'data': [
                round(r['hit_songs'] / r['active_songs'] * 100, 1) if r['active_songs'] else None
                for r in rows
            ],
        }],
    }


_CHART_BUILDERS = {
    'streams-over-time': lambda year=None, country=None: _streams_over_time_chart(country=country),
    'top-countries': lambda year=None, country=None: _top_countries_chart(year=year),
    'top-artists': _top_artists_chart,
    'hit-rate-trend': lambda year=None, country=None: _hit_rate_trend_chart(country=country),
}


def get_chart_data(chart_key, year=None, country=None):
    builder = _CHART_BUILDERS.get(chart_key)
    return builder(year=year, country=country) if builder else None

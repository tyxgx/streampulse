"""
Read-only models over the Gold-layer Postgres tables (see schema.sql).

managed=False: Django does not own these tables' schema. schema.sql and
scripts/load_gold_to_postgres.py are the source of truth — the ETL job is a
truncate-and-reload batch process, not something Django migrations should
track. These models exist purely for querying via the 'gold' database alias.

primary_key=True below is a nominal Django-ORM requirement, not a real
uniqueness guarantee (every table here has a composite real PK in
schema.sql, e.g. (country_name, year_month)) — matches the convention the
original version of this file already used. Fine for these read-only,
explicit .filter()/.values()/.annotate() query patterns; never relied on
for .get()-style uniqueness.
"""
from django.db import models


class CountryPerformance(models.Model):
    year = models.IntegerField()
    month = models.IntegerField()
    year_month = models.CharField(max_length=16)
    country_name = models.CharField(max_length=128, primary_key=True)
    total_streams = models.BigIntegerField(null=True)
    active_songs = models.BigIntegerField(null=True)
    hit_songs = models.BigIntegerField(null=True)
    avg_chart_strength = models.FloatField(null=True)
    active_artists = models.BigIntegerField(null=True)
    monthly_total_streams = models.BigIntegerField(null=True)
    top_song_name = models.CharField(max_length=255, null=True)
    top_artist_name = models.CharField(max_length=255, null=True)
    growth_percentage = models.FloatField(null=True)

    class Meta:
        managed = False
        db_table = 'country_performance'


class KpiArtist(models.Model):
    """Country x artist x month presence — no metric columns exist in the
    source, this is a dimension table only (which artists appeared in which
    country/month), used for entity-existence/count queries, not narrative
    RAG chunks."""
    year = models.IntegerField()
    month = models.IntegerField()
    year_month = models.CharField(max_length=16)
    country_name = models.CharField(max_length=128)
    artist_uri = models.CharField(max_length=64, primary_key=True)

    class Meta:
        managed = False
        db_table = 'kpi_artist'


class KpiSong(models.Model):
    year = models.IntegerField()
    month = models.IntegerField()
    year_month = models.CharField(max_length=16)
    country_name = models.CharField(max_length=128)
    uri = models.CharField(max_length=64, primary_key=True)
    standardized_label = models.CharField(max_length=255, null=True)
    total_streams = models.BigIntegerField(null=True)
    is_hit = models.IntegerField(null=True)

    class Meta:
        managed = False
        db_table = 'kpi_song'


class LabelPerformanceEnhanced(models.Model):
    year = models.IntegerField()
    month = models.IntegerField()
    year_month = models.CharField(max_length=16)
    country_name = models.CharField(max_length=128)
    standardized_label = models.CharField(max_length=255, primary_key=True)
    total_streams = models.BigIntegerField(null=True)
    active_songs = models.BigIntegerField(null=True)
    active_artists = models.BigIntegerField(null=True)

    class Meta:
        managed = False
        db_table = 'label_performance_enhanced'


class MonthlyTrends(models.Model):
    """Country x month grain (was a single global-aggregate row per month
    in the old source, hence year_month used to be the natural PK — now
    country_name is used instead, see module docstring)."""
    year = models.IntegerField()
    month = models.IntegerField()
    year_month = models.CharField(max_length=16)
    country_name = models.CharField(max_length=128, primary_key=True)
    total_streams = models.BigIntegerField(null=True)
    active_songs = models.BigIntegerField(null=True)
    active_labels = models.BigIntegerField(null=True)
    hit_songs = models.BigIntegerField(null=True)
    avg_chart_strength = models.FloatField(null=True)
    active_artists = models.BigIntegerField(null=True)
    growth_percentage = models.FloatField(null=True)

    class Meta:
        managed = False
        db_table = 'monthly_trends'


class ArtistPerformance(models.Model):
    """Country x artist x month, with real metrics — unlike kpi_artist, this
    is built from the Silver layer (scripts/build_artist_gold.py), not the
    original Gold source. See schema.sql for details."""
    year = models.IntegerField()
    month = models.IntegerField()
    year_month = models.CharField(max_length=16)
    country_name = models.CharField(max_length=128)
    artist_uri = models.CharField(max_length=64, primary_key=True)
    artist_name = models.CharField(max_length=255, null=True)
    total_streams = models.BigIntegerField(null=True)
    track_count = models.BigIntegerField(null=True)
    hit_track_count = models.BigIntegerField(null=True)
    best_rank = models.IntegerField(null=True)

    class Meta:
        managed = False
        db_table = 'artist_performance'


class TrackCatalog(models.Model):
    """uri -> track_name lookup, also built from Silver. No metrics — used
    only to enrich RAG chunk text with real track names."""
    uri = models.CharField(max_length=64, primary_key=True)
    track_name = models.CharField(max_length=255, null=True)

    class Meta:
        managed = False
        db_table = 'track_catalog'

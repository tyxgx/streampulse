"""
Views for the team app.

Member data comes from apps/team/services.py so the roster can move from
a hardcoded list to a database-backed model (or an external source) later
without touching this view or its template.
"""
from django.views.generic import TemplateView

from . import services


class TeamView(TemplateView):
    template_name = 'team/team.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['members'] = services.get_team_members()
        return context

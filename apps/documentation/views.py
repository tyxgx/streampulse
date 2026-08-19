"""
Views for the documentation app.

Doc content is served through get_context_data() backed by
apps/documentation/services.py, which currently returns a hardcoded list
of doc cards and can later be swapped for markdown files or a CMS without
changing this view or its template.
"""
from django.views.generic import TemplateView

from . import services


class DocumentationView(TemplateView):
    template_name = 'documentation/documentation.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['docs'] = services.get_documentation_cards()
        return context

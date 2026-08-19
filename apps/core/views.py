"""
Views for the core app: Home, About, Architecture.

These pages are informational/static today. If a page later needs dummy
or live data, give it a get_context_data() override backed by a small
service module — see apps/gold_data/services.py for the pattern this
project follows once real data enters the picture.
"""
from django.views.generic import TemplateView

from . import services


class HomeView(TemplateView):
    template_name = 'core/home.html'


class AboutView(TemplateView):
    template_name = 'core/about.html'


class ArchitectureView(TemplateView):
    template_name = 'core/architecture.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['progress'] = services.get_progress_markers()
        context['flow'] = services.get_architecture_flow()
        context['layers'] = services.get_layer_details()
        context['gold_stats'] = services.get_gold_stats()
        context['gold_tables'] = services.get_gold_tables()
        context['grain'] = services.get_grain_examples()
        context['rag_concept'] = services.get_rag_concept()
        context['rag_flow_overview'] = services.get_rag_flow_overview()
        context['rag_pipeline'] = services.get_rag_pipeline_steps()
        context['tech_stack'] = services.get_tech_stack()
        context['deployment_flow'] = services.get_deployment_flow()
        return context


class RagPipelineWalkthroughView(TemplateView):
    """Standalone, deliberately unlinked presentation page — reachable
    only by typing the URL directly, not from the navbar or footer (the
    nav's Project dropdown is intentionally capped at exactly 2 items).
    Renders the same RAG pipeline diagram + narrative twice, in two
    unnamed sections, so each of the two live presenters has their own
    place to open and read from on stage."""
    template_name = 'core/rag_pipeline_walkthrough.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['rag_flow_overview'] = services.get_rag_flow_overview()
        context['narrative'] = services.get_rag_walkthrough_narrative()
        return context


class ProjectSummaryView(TemplateView):
    """A single shareable overview page — deliberately unlinked (same
    reasoning as RagPipelineWalkthroughView above), meant to be sent
    directly as a URL to someone with no prior context on the project."""
    template_name = 'core/summary.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['gold_stats'] = services.get_gold_stats()
        context['rag_pipeline'] = services.get_rag_pipeline_steps()
        context['capabilities'] = services.get_project_summary_capabilities()
        context['tech_stack'] = services.get_tech_stack()
        return context

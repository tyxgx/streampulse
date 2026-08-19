"""URL routes for the core app."""
from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = 'core'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('about/', views.AboutView.as_view(), name='about'),
    path('architecture/', views.ArchitectureView.as_view(), name='architecture'),
    # Deliberately NOT linked from navbar.html/footer.html — the nav's
    # Project dropdown is capped at exactly 2 items. Reachable only by
    # typing this URL directly; a presentation tool for the two live
    # speakers, not a public marketing page.
    path('rag-pipeline/', views.RagPipelineWalkthroughView.as_view(), name='rag_pipeline_walkthrough'),
    # Also deliberately unlinked — a shareable one-pager for sending
    # directly to someone outside the team, not part of site navigation.
    path('summary/', views.ProjectSummaryView.as_view(), name='summary'),
    # Pipeline Overview was folded into the Architecture page — this route
    # stays alive as a permanent redirect so any existing links/bookmarks
    # don't 404.
    path('pipeline-overview/', RedirectView.as_view(pattern_name='core:architecture', permanent=True), name='pipeline_overview'),
]

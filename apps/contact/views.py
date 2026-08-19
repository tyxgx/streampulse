"""
Views for the contact app.

The form posts to this same view; submission handling is isolated in
_handle_submission() so it can be swapped for a real email/CRM/ticketing
integration later without touching the template or URL contract.
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View


class ContactView(View):
    template_name = 'contact/contact.html'

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        self._handle_submission(request)
        messages.success(request, "Thanks for reaching out! We'll get back to you soon.")
        return redirect('contact:index')

    def _handle_submission(self, request):
        """
        STUB: currently a no-op beyond validation. Wire this up to a real
        email backend, CRM, or ticketing system when one is available.
        """
        pass

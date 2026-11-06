from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.forms import CommitteeDocumentForm
from src.decorators import officer_required
from django.core.exceptions import ValidationError
from src.utils.file_validation import validate_uploaded_file


class ReportRefused(Exception):
    """
    The upload was refused. ``web_message`` is the page's own banner text;
    ``detail`` names the actual problem for the app (the page shows it beside
    the field instead); ``form`` is the bound form to re-render, or None for
    a blank one (a file that fails ``validate_uploaded_file``).
    """

    def __init__(self, web_message, detail, form=None):
        super().__init__(detail)
        self.web_message = web_message
        self.detail = detail
        self.form = form


def _first_form_error(form):
    for name, errors in form.errors.items():
        field = form.fields.get(name)
        label = field.label if field is not None and field.label else None
        return f'{label}: {errors[0]}' if label else str(errors[0])
    return 'Please check the form and try again.'


def create_officer_report(user, data, files):
    """
    The Upload Report page's save (``upload_report``), shared with the app
    (src/mobile/views_officer_tools.ReportUploadView): ``validate_uploaded_file``
    on the file, then ``CommitteeDocumentForm`` (its own extension, size and
    MIME checks). Returns the new ``CommitteeDocument``; raises ``ReportRefused``.
    """
    if 'document' in files:
        try:
            validate_uploaded_file(files['document'])
        except ValidationError as e:
            raise ReportRefused(f'File upload error: {str(e)}', ' '.join(e.messages))

    form = CommitteeDocumentForm(data, files)
    if not form.is_valid():
        raise ReportRefused(
            'There was an error uploading the document. Please check the form and try again.',
            _first_form_error(form), form=form)
    document = form.save(commit=False)
    document.uploaded_by = user
    document.save()
    return document


def report_uploaded_message(document):
    return f'Document "{document.title}" has been uploaded successfully!'


@login_required
@officer_required
def upload_report(request):
    """View for officers to upload committee reports and documents"""
    if request.method == 'POST':
        try:
            document = create_officer_report(request.user, request.POST, request.FILES)
        except ReportRefused as refused:
            messages.error(request, refused.web_message)
            return render(request, 'officer/upload_report.html', {
                'form': refused.form if refused.form is not None else CommitteeDocumentForm(),
            })
        messages.success(request, report_uploaded_message(document))
        return redirect('officer_home')
    else:
        form = CommitteeDocumentForm()

    context = {
        'form': form,
    }

    return render(request, 'officer/upload_report.html', context)

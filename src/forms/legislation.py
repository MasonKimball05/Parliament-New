from django import forms
import magic  # python-magic for MIME type detection

from src.models import Legislation, LegislationDraft


class LegislationForm(forms.ModelForm):
    class Meta:
        model = Legislation
        fields = ['title', 'description', 'available_at', 'voting_starts_at', 'voting_ends_at', 'document', 'anonymous_vote', 'allow_abstain', 'required_percentage']
        widgets = {
            'available_at': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'voting_starts_at': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'voting_ends_at': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
        }
        help_texts = {
            'available_at': 'When the document becomes visible for review.',
            'voting_starts_at': 'Optional: When voting opens. Leave blank to start voting when document is available.',
            'voting_ends_at': 'Optional: Voting will automatically close at this time. Leave blank for manual close only.',
            'document': 'Optional if you provide a detailed description (20+ characters).',
        }

    def clean(self):
        cleaned_data = super().clean()
        document = cleaned_data.get('document')
        description = cleaned_data.get('description', '').strip()

        # Require either a document OR a meaningful description (at least 20 characters)
        if not document and len(description) < 20:
            raise forms.ValidationError(
                'Please either upload a document OR provide a detailed description (at least 20 characters).'
            )

        return cleaned_data

    def clean_document(self):
        file = self.cleaned_data.get('document')
        if file:
            # Check file extension
            if not file.name.lower().endswith(('.pdf', '.docx')):
                raise forms.ValidationError('Only PDF and DOCX files are allowed.')

            # Check file size (20 MB max)
            if file.size > 20 * 1024 * 1024:
                raise forms.ValidationError('File size must not exceed 20 MB.')

            # Check MIME type to prevent file extension spoofing
            try:
                mime = magic.from_buffer(file.read(2048), mime=True)
                file.seek(0)  # Reset file pointer

                allowed_mimes = [
                    'application/pdf',
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                ]

                if mime not in allowed_mimes:
                    raise forms.ValidationError(
                        f'Invalid file type. Expected PDF or DOCX, but got {mime}.'
                    )
            except Exception as e:
                raise forms.ValidationError('Unable to verify file type. Please try again.')

        return file


class LegislationDraftForm(forms.ModelForm):
    """
    v3.19.0 — the My Work draft editor.

    ⚠️ THIS FORM IS DELIBERATELY MORE PERMISSIVE THAN `LegislationForm`, and the
    asymmetry is the point. `LegislationForm.clean` requires a document OR 20+
    characters of description, because a bill going to the chapter must be
    readable. A *draft* is a work in progress — refusing to save an unfinished
    one is how you get people writing bills in a Google Doc instead.

    The floor is not dropped, only moved: `LegislationDraft.ready_to_publish()`
    applies exactly the same rule at publish time, and the My Work page greys
    out the publish button and says which half is missing. Validation belongs at
    the boundary the promise is made at, not at every keystroke before it.
    """

    class Meta:
        model = LegislationDraft
        fields = [
            'title', 'description', 'document',
            'planned_available_at', 'planned_voting_ends_at',
            'notes', 'vote_mode', 'required_percentage',
            'anonymous_vote', 'allow_abstain',
        ]
        widgets = {
            'planned_available_at': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'planned_voting_ends_at': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'description': forms.Textarea(attrs={
                'rows': 6,
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'notes': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Only you can see this. Not copied to the published bill.',
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
            'title': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            }),
        }
        help_texts = {
            'planned_available_at': 'When you intend to present this. Leave blank while you are still deciding — you will need it to publish.',
            'planned_voting_ends_at': 'Optional: when voting should close automatically.',
            'document': 'Optional while drafting. PDF or DOCX, 20 MB max.',
            'notes': 'Private to you. Never copied to the published bill.',
        }

    def clean_document(self):
        file = self.cleaned_data.get('document')
        if not file:
            return file

        if not file.name.lower().endswith(('.pdf', '.docx')):
            raise forms.ValidationError('Only PDF and DOCX files are allowed.')

        if file.size > 20 * 1024 * 1024:
            raise forms.ValidationError('File size must not exceed 20 MB.')

        # MIME sniff to stop extension spoofing — same check as LegislationForm.
        # It matters here even though a draft is private, because publish copies
        # the file across to the real bill WITHOUT re-validating it. This is the
        # only place the draft's document is ever checked.
        try:
            mime = magic.from_buffer(file.read(2048), mime=True)
            file.seek(0)
            allowed_mimes = [
                'application/pdf',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            ]
            if mime not in allowed_mimes:
                raise forms.ValidationError(
                    f'Invalid file type. Expected PDF or DOCX, but got {mime}.'
                )
        except forms.ValidationError:
            # v3.19.0 — re-raise BEFORE the bare handler below. LegislationForm's
            # version of this block swallows its own ValidationError into the
            # generic "Unable to verify file type" message, which is why a
            # spoofed .pdf reports a confusing error there. Do not copy that.
            raise
        except Exception:
            raise forms.ValidationError('Unable to verify file type. Please try again.')

        return file

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ⚠️ v3.19.5 — READ BEFORE ANYTHING CAN OVERWRITE IT. Django's FileField
        # descriptor replaces `instance.document` in place when the bound form is
        # cleaned, so by the time `save()` runs the old storage name is gone with
        # no record of it anywhere. Same ordering constraint as
        # `publish_legislation_draft`'s `private_original`, which is captured
        # above its `atomic()` block for exactly this reason — that is now the
        # second instance of this shape in one feature, and the reason it is
        # spelled out in both places.
        self._document_name_on_load = (
            self.instance.document.name
            if self.instance and self.instance.pk and self.instance.document
            else ''
        )

    def save(self, commit=True):
        """
        Record the uploaded filename, and unlink the file this save replaces.

        v3.19.3: `LegislationDraft.document` now stores `<uuid>.<ext>` (see
        `legislation_draft_upload_path`), so the author's own filename is lost
        at save time unless it is captured here. This is the only place it is
        still available — `upload_to` receives it but has nowhere to put it.

        Guarded on `changed_data` so editing a draft's title does not blank the
        name recorded when the file was uploaded two weeks ago.

        ⚠️ v3.19.5 — THE OTHER TWO WAYS A DRAFT FILE STOPS BEING REFERENCED.
        v3.19.4 gave a draft attachment "exactly one lifetime" and covered the
        row being **deleted** (a `post_delete` receiver) and the draft being
        **published** (unlink the private original once the copy is durable).
        Both correct, and between them they covered neither of the two things a
        member actually does from the edit form: **replacing** an attachment
        writes a fresh uuid and simply overwrites the field, and **clearing** it
        empties the field — in both cases leaving the previous file on disk with
        nothing in the database naming it, ever again. The test class asserting
        the property was called `ADraftAttachmentHasExactlyOneLifetime` and the
        property did not hold.

        The lesson worth keeping, because this codebase has now paid for it in
        five consecutive releases in a different form: **enumerate the ways a
        reference can END, not the ways you happen to have written code for.**
        Delete is the one that looks like cleanup, so it is the one that gets
        cleanup written for it; replace and clear are edits, and an edit does not
        look like a deletion until you ask what happened to the bytes.
        """
        instance = super().save(commit=False)

        if 'document' in self.changed_data:
            uploaded = self.cleaned_data.get('document')
            # Cleared the attachment → clear the remembered name with it, or the
            # next upload inherits the old one.
            instance.document_original_name = getattr(uploaded, 'name', '') or ''

        if commit:
            instance.save()
            self.save_m2m()
            self._unlink_replaced_document(instance)
        return instance

    def _unlink_replaced_document(self, instance):
        """
        Remove the file this save orphaned, if it orphaned one.

        Only ever called after a committed save. `commit=False` is the create
        path (`create_legislation_draft`), where there is no previous file by
        definition — and calling this before the row is written would delete a
        file the unsaved row still points at if the save then failed.

        `on_commit` rather than inline, matching `publish_legislation_draft`: a
        rollback after this point must not have destroyed the file the restored
        row still references. NOTE FOR TESTS: `on_commit` callbacks do not run
        under `TestCase` unless wrapped in
        `self.captureOnCommitCallbacks(execute=True)`.

        The name comparison is load-bearing and not paranoia. `changed_data` can
        contain `'document'` when the stored name did not actually change (a
        re-submitted unchanged `ClearableFileInput` under some widget states),
        and `upload_to` collisions are resolved by Django with a random suffix
        rather than by reuse — so "changed" and "points somewhere else" are
        different questions and only the second one licenses an unlink.
        """
        from django.db import transaction

        # Local import: `src.models.legislation` imports storage and settings at
        # module scope, and forms are imported early enough that hoisting this
        # to the top would widen the import graph for one helper.
        from src.models.legislation import delete_draft_document_file

        previous = self._document_name_on_load
        current = instance.document.name if instance.document else ''

        if not previous or previous == current:
            return

        # Keep the form's own idea of the file in step with the database, so a
        # form re-used after save (or saved twice) cannot schedule the same
        # unlink a second time.
        self._document_name_on_load = current

        transaction.on_commit(lambda: delete_draft_document_file(previous))

from django import forms

from src.models import ParliamentUser, Role, UserPreferences


class ForcedPasswordChangeForm(forms.Form):
    """Form for users who must change their password"""
    old_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500',
            'placeholder': 'Current password'
        }),
        label='Current Password'
    )
    new_password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500',
            'placeholder': 'New password'
        }),
        label='New Password',
        help_text='Password must be at least 9 characters with uppercase, lowercase, number, and special character.'
    )
    new_password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500',
            'placeholder': 'Confirm new password'
        }),
        label='Confirm New Password'
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        """Verify the old password is correct"""
        old_password = self.cleaned_data.get('old_password')
        if not self.user.check_password(old_password):
            raise forms.ValidationError('Your current password is incorrect.')
        return old_password

    def clean_new_password2(self):
        """Verify the two password fields match"""
        password1 = self.cleaned_data.get('new_password1')
        password2 = self.cleaned_data.get('new_password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('The two password fields must match.')
        return password2

    def save(self):
        """Save the new password and clear the force_password_change flag"""
        from django.contrib.auth.password_validation import validate_password
        password = self.cleaned_data['new_password1']

        # Validate password against Django's password validators
        validate_password(password, self.user)

        self.user.set_password(password)
        self.user.force_password_change = False
        self.user.has_default_password = False
        self.user.save()
        return self.user


class UserPreferencesForm(forms.Form):
    """
    Form for users to update their preferences.

    Accepts an ``instance`` kwarg (a UserPreferences object) for compatibility
    with the existing preferences_view, which calls:
        UserPreferencesForm(request.POST, instance=preferences)
        UserPreferencesForm(instance=preferences)
    The ``save()`` method writes cleaned data back into instance.prefs and saves.
    """
    _CB = "h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700"
    _CB_MENU = "h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 menu-checkbox"

    # Theme
    theme = forms.ChoiceField(
        choices=UserPreferences.THEME_CHOICES,
        label="Color Theme",
        widget=forms.Select(attrs={
            "class": "mt-1 block w-full rounded-md border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm focus:border-primary-500 focus:ring-primary-500"
        }),
    )

    # Email notifications
    email_announcements = forms.BooleanField(required=False, label="Announcements",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    email_legislation = forms.BooleanField(required=False, label="New Legislation",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    email_events = forms.BooleanField(required=False, label="Upcoming Events",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    email_committee_updates = forms.BooleanField(required=False, label="Committee Updates",
        widget=forms.CheckboxInput(attrs={"class": _CB}))

    # Display
    show_announcement_popups = forms.BooleanField(required=False, label="Show announcement popups",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    compact_view = forms.BooleanField(required=False, label="Compact view mode",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    home_layout = forms.ChoiceField(
        choices=[('modern', 'Modern (default)'), ('classic', 'Classic')],
        label="Home Page Layout",
        widget=forms.Select(attrs={
            "class": "mt-1 block w-full rounded-md border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm focus:border-primary-500 focus:ring-primary-500"
        }),
    )
    landing_page = forms.ChoiceField(
        choices=[
            ('home', 'Home'),
            ('announcements', 'Announcements'),
            ('calendar', 'Calendar'),
            ('vote', 'Vote'),
        ],
        label="Landing Page After Login",
        widget=forms.Select(attrs={
            "class": "mt-1 block w-full rounded-md border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm focus:border-primary-500 focus:ring-primary-500"
        }),
    )

    # In-app notifications
    notify_announcements = forms.BooleanField(required=False, label="Announcements",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    notify_legislation = forms.BooleanField(required=False, label="Legislation & Voting",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    notify_events = forms.BooleanField(required=False, label="Events",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    notify_slating = forms.BooleanField(required=False, label="Officer Elections (Slating)",
        widget=forms.CheckboxInput(attrs={"class": _CB}))

    # Push notification per-type preferences
    push_announcements = forms.BooleanField(required=False, label="Announcements",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    push_legislation = forms.BooleanField(required=False, label="Legislation & Voting",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    push_events = forms.BooleanField(required=False, label="Events",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    push_slating = forms.BooleanField(required=False, label="Officer Elections (Slating)",
        widget=forms.CheckboxInput(attrs={"class": _CB}))
    push_chat = forms.BooleanField(required=False, label="Chat Messages",
        widget=forms.CheckboxInput(attrs={"class": _CB}))

    # Menu
    show_vote_menu = forms.BooleanField(required=False, label="Show Vote",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_committees_menu = forms.BooleanField(required=False, label="Show Committees",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_chats_menu = forms.BooleanField(required=False, label="Show Chats",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_documents_menu = forms.BooleanField(required=False, label="Show Documents",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_announcements_menu = forms.BooleanField(required=False, label="Show Announcements",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_calendar_menu = forms.BooleanField(required=False, label="Show Calendar",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_legislation_menu = forms.BooleanField(required=False, label="Show Legislation",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_cnb_menu = forms.BooleanField(required=False, label="Show Constitution & Bylaws",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_resolutions_menu = forms.BooleanField(required=False, label="Show Resolutions",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_excuses_menu = forms.BooleanField(required=False, label="Show My Excuses",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_search_menu = forms.BooleanField(required=False, label="Show Search",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))
    show_roberts_rules_menu = forms.BooleanField(required=False, label="Show Robert's Rules",
        widget=forms.CheckboxInput(attrs={"class": _CB_MENU}))

    def __init__(self, data=None, instance=None, **kwargs):
        self._instance = instance
        if instance is not None and data is None:
            kwargs['initial'] = {
                'theme': instance.theme,
                'email_announcements': instance.email_announcements,
                'email_legislation': instance.email_legislation,
                'email_events': instance.email_events,
                'email_committee_updates': instance.email_committee_updates,
                'show_announcement_popups': instance.show_announcement_popups,
                'compact_view': instance.compact_view,
                'home_layout': instance.home_layout,
                'landing_page': instance.landing_page,
                'notify_announcements': instance.notify_announcements,
                'notify_legislation': instance.notify_legislation,
                'notify_events': instance.notify_events,
                'notify_slating': instance.notify_slating,
                'push_announcements': instance.push_announcements,
                'push_legislation': instance.push_legislation,
                'push_events': instance.push_events,
                'push_slating': instance.push_slating,
                'push_chat': instance.push_chat,
                'show_vote_menu': instance.show_vote_menu,
                'show_committees_menu': instance.show_committees_menu,
                'show_chats_menu': instance.show_chats_menu,
                'show_documents_menu': instance.show_documents_menu,
                'show_announcements_menu': instance.show_announcements_menu,
                'show_calendar_menu': instance.show_calendar_menu,
                'show_legislation_menu': instance.show_legislation_menu,
                'show_cnb_menu': instance.show_cnb_menu,
                'show_resolutions_menu': instance.show_resolutions_menu,
                'show_excuses_menu': instance.show_excuses_menu,
                'show_search_menu': instance.show_search_menu,
                'show_roberts_rules_menu': instance.show_roberts_rules_menu,
            }
        super().__init__(data, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        menu_fields = [
            'show_vote_menu', 'show_committees_menu', 'show_chats_menu',
            'show_documents_menu', 'show_announcements_menu', 'show_calendar_menu',
            'show_legislation_menu', 'show_cnb_menu', 'show_resolutions_menu', 'show_excuses_menu',
            'show_search_menu', 'show_roberts_rules_menu',
        ]
        selected_count = sum(1 for f in menu_fields if cleaned_data.get(f))
        if selected_count > 9:
            raise forms.ValidationError(
                f"You can select at most 9 menu items. "
                f"You selected {selected_count}; please deselect {selected_count - 9}."
            )
        return cleaned_data

    # Sections of `prefs` that this form does not manage and must not destroy.
    # `save()` rebuilds prefs wholesale, so anything set by another surface has
    # to be carried across explicitly or it is silently wiped the next time the
    # user touches this page. 'dev' is written by the gated toggle_dev_mode
    # endpoint (developer mode), never by this form.
    PRESERVED_SECTIONS = ('dev',)

    def save(self):
        """Write cleaned data into instance.prefs and save. Returns the instance."""
        p = self._instance
        preserved = {
            section: (p.prefs or {})[section]
            for section in self.PRESERVED_SECTIONS
            if section in (p.prefs or {})
        }
        p.theme = self.cleaned_data['theme']
        p.prefs = {
            'email': {
                'announcements': self.cleaned_data['email_announcements'],
                'legislation': self.cleaned_data['email_legislation'],
                'events': self.cleaned_data['email_events'],
                'committee_updates': self.cleaned_data['email_committee_updates'],
            },
            'display': {
                'compact_view': self.cleaned_data['compact_view'],
                'announcement_popups': self.cleaned_data['show_announcement_popups'],
                'home_layout': self.cleaned_data['home_layout'],
                'landing_page': self.cleaned_data['landing_page'],
            },
            'menu': {
                'vote': self.cleaned_data['show_vote_menu'],
                'committees': self.cleaned_data['show_committees_menu'],
                'chats': self.cleaned_data['show_chats_menu'],
                'documents': self.cleaned_data['show_documents_menu'],
                'announcements': self.cleaned_data['show_announcements_menu'],
                'calendar': self.cleaned_data['show_calendar_menu'],
                'legislation': self.cleaned_data['show_legislation_menu'],
                'cnb': self.cleaned_data['show_cnb_menu'],
                'resolutions': self.cleaned_data['show_resolutions_menu'],
                'excuses': self.cleaned_data['show_excuses_menu'],
                'search': self.cleaned_data['show_search_menu'],
                'roberts_rules': self.cleaned_data['show_roberts_rules_menu'],
            },
            'notifications': {
                'announcements': self.cleaned_data['notify_announcements'],
                'legislation': self.cleaned_data['notify_legislation'],
                'events': self.cleaned_data['notify_events'],
                'slating': self.cleaned_data['notify_slating'],
            },
            'push': {
                'announcements': self.cleaned_data['push_announcements'],
                'legislation': self.cleaned_data['push_legislation'],
                'events': self.cleaned_data['push_events'],
                'slating': self.cleaned_data['push_slating'],
                'chat': self.cleaned_data['push_chat'],
            },
        }
        p.prefs.update(preserved)
        p.save()
        return p


class AddMemberForm(forms.Form):
    """Form for officers to add new members"""
    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'Full name (e.g., John Smith)'
        })
    )
    # ⚠️ v3.23.0 — OPTIONAL. Leave it blank and `generate_member_uid()` assigns
    # one. It used to be required free text, which is how `P-C7JKZY` came to be
    # a convention somebody typed rather than a mechanism.
    #
    # The old help text said *"cannot be changed later"*. That was false for
    # pledges — initiation changed it, at the cost of 180 lines of raw SQL — and
    # true for everyone else. It is now true for everyone, which is what makes
    # this field safe to use as a primary key. See `ParliamentUser.user_id`.
    user_id = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'Leave blank to generate one'
        }),
        help_text='Permanent internal ID. Leave blank and one will be generated. '
                  'This is NOT the roll number — set that at initiation.'
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
            'placeholder': 'email@example.com (optional)'
        })
    )
    member_type = forms.ChoiceField(
        choices=ParliamentUser.MEMBER_TYPES,
        initial='Pledge',
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        })
    )
    member_status = forms.ChoiceField(
        choices=ParliamentUser.MEMBER_STATUS,
        initial='Active',
        widget=forms.Select(attrs={
            'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
        })
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 dark:border-gray-600 rounded'
        })
    )

    def clean_user_id(self):
        """
        Generate one when the officer leaves it blank (v3.23.0).

        ⚠️ Generated HERE rather than in the view, so that every caller of this
        form gets the same behaviour — including any future one. The view used
        to be the only writer and that is exactly the assumption this codebase
        keeps being wrong about.
        """
        user_id = (self.cleaned_data.get('user_id') or '').strip()
        if not user_id:
            from src.models.users import generate_member_uid
            return generate_member_uid()
        if ParliamentUser.objects.filter(user_id=user_id).exists():
            raise forms.ValidationError('A member with this ID already exists.')
        return user_id

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and ParliamentUser.objects.filter(email=email).exists():
            raise forms.ValidationError('A member with this email already exists.')
        return email


class EditMemberForm(forms.ModelForm):
    """Form for officers to edit existing members"""
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 dark:border-gray-600 rounded'
        })
    )

    class Meta:
        model = ParliamentUser
        fields = ['name', 'preferred_name', 'email', 'member_type', 'member_status', 'roles']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'preferred_name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent',
                'placeholder': 'Optional preferred first name'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'member_type': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
            'member_status': forms.Select(attrs={
                'class': 'w-full px-4 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial['roles'] = self.instance.roles.all()

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            existing = ParliamentUser.objects.filter(email=email).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError('A member with this email already exists.')
        return email

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            # Handle roles separately since it's a ManyToMany field
            instance.roles.set(self.cleaned_data.get('roles', []))
        return instance

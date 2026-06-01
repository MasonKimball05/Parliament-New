"""
API serializers for Parliament's read-only public endpoints.

Field selection is intentional — sensitive fields (email, phone, security flags,
password hash, internal admin fields) are excluded from all serializers.
"""
from rest_framework import serializers

from src.models.users import ParliamentUser, Role
from src.models.events import Event
from src.models.legislation import Legislation


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ['id', 'name']


class MemberSerializer(serializers.ModelSerializer):
    """
    Read-only member directory entry.

    Omits: email, phone_number, password, security/admin flags, watch_flag,
    backup_codes_acknowledged, force_password_change, is_quarantined, etc.
    """
    roles = RoleSerializer(many=True, read_only=True)
    display_name = serializers.SerializerMethodField()
    profile_picture_url = serializers.SerializerMethodField()

    class Meta:
        model = ParliamentUser
        fields = [
            'user_id',
            'name',
            'preferred_name',
            'display_name',
            'member_type',
            'member_status',
            'roles',
            'role_number',
            'about_me',
            'majors',
            'minors',
            'concentrations',
            'pledge_class',
            'pledge_class_greek',
            'graduation_year',
            'graduation_semester',
            'instagram',
            'twitter',
            'profile_picture_url',
        ]
        read_only_fields = fields

    def get_display_name(self, obj):
        return obj.get_display_name() if hasattr(obj, 'get_display_name') else obj.name

    def get_profile_picture_url(self, obj):
        if not obj.profile_picture:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(obj.profile_picture.url)
        return obj.profile_picture.url


class EventSerializer(serializers.ModelSerializer):
    """Read-only event list entry."""
    created_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Event
        fields = [
            'id',
            'title',
            'description',
            'date_time',
            'location',
            'visible_to',
            'is_recurring',
            'recurrence_type',
            'recurrence_interval',
            'recurrence_unit',
            'recurrence_days',
            'created_by',
            'created_at',
        ]
        read_only_fields = fields


class LegislationSerializer(serializers.ModelSerializer):
    """Read-only legislation list entry. Vote tallies are omitted for anonymous ballots."""
    posted_by = serializers.StringRelatedField(read_only=True)
    co_authors = serializers.StringRelatedField(many=True, read_only=True)

    class Meta:
        model = Legislation
        fields = [
            'id',
            'title',
            'description',
            'status',
            'posted_by',
            'co_authors',
            'required_percentage',
            'vote_mode',
            'allow_abstain',
            'anonymous_vote',
            'available_at',
            'voting_starts_at',
            'voting_ends_at',
            'voting_ended_at',
            'voting_closed',
            'passed',
            'created_at',
        ]
        read_only_fields = fields

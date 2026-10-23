"""
`delete_channel` GET rendered a template that did not exist.

The "🗑️ Delete Channel" control on `chat/index.html` is a plain link, so an
admin's click is a GET, and the view answered it by rendering
`chat/delete_channel.html` — which had never been written. Every click was a
500 (TemplateDoesNotExist). The POST branch worked, which is why the bug only
showed up by hand (found 10-22-26 on Parliament-Dev).

`test_detail_route_smoke` requests this route as an admin but missed it: its
fixture channel is a committee channel, so the GET took the "cannot delete
committee channels" redirect and never reached the render. The custom-channel
GET below is the case it could not see.
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import ChatChannel, ChatMessage, Committee, ParliamentUser


def make_user(uid, is_admin=False):
    return ParliamentUser.objects.create(
        user_id=uid, name=f'User {uid}', username=uid,
        member_type='Member', member_status='Active', is_admin=is_admin,
    )


class DeleteChannelTests(TestCase):
    def setUp(self):
        self.admin = make_user('dc-admin', is_admin=True)
        self.member = make_user('dc-member')

        self.custom = ChatChannel.objects.create(
            name='Formal Planning', description='Planning the spring formal',
            channel_type='custom', access_type='open', created_by=self.admin)
        ChatMessage.objects.create(
            channel=self.custom, sender=self.admin, message='hi')

        committee = Committee.objects.create(name='Social', code='DCS')
        self.committee_channel = ChatChannel.objects.create(
            name='Social', channel_type='committee', access_type='committee',
            committee=committee)

        self.client = Client()

    def url(self, channel):
        return reverse('delete_channel', args=[channel.id])

    def test_admin_get_shows_the_confirm_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url(self.custom))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'chat/delete_channel.html')
        self.assertContains(response, 'Formal Planning')
        self.assertContains(response, 'method="post"')
        # Rendering the confirm page must not delete anything.
        self.assertTrue(ChatChannel.objects.filter(pk=self.custom.pk).exists())

    def test_admin_post_deletes_a_custom_channel(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url(self.custom))

        self.assertRedirects(response, reverse('chat_index'),
                             fetch_redirect_response=False)
        self.assertFalse(ChatChannel.objects.filter(pk=self.custom.pk).exists())
        self.assertFalse(ChatMessage.objects.filter(channel_id=self.custom.pk).exists())

    def test_committee_channel_cannot_be_deleted(self):
        self.client.force_login(self.admin)
        for method in (self.client.get, self.client.post):
            response = method(self.url(self.committee_channel))
            self.assertRedirects(response, reverse('chat_index'),
                                 fetch_redirect_response=False)
        self.assertTrue(
            ChatChannel.objects.filter(pk=self.committee_channel.pk).exists())

    def test_non_admin_gets_403(self):
        self.client.force_login(self.member)
        for method in (self.client.get, self.client.post):
            response = method(self.url(self.custom))
            self.assertEqual(response.status_code, 403)
        self.assertTrue(ChatChannel.objects.filter(pk=self.custom.pk).exists())

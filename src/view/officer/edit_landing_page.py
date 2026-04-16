import json
import logging
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from src.decorators import officer_or_advisor_required
from src.feature_flag_decorators import require_page_enabled
from src.models import (
    LandingPageContent, LandingPagePhoto,
    LandingPageContactTopic, LandingPageFormLink, LandingPageSocialLink,
)

logger = logging.getLogger('function_calls')


@login_required
@officer_or_advisor_required
@require_page_enabled('officer_home')
@require_http_methods(["GET", "POST"])
def edit_landing_page(request):
    """Officer editor for the public landing page content."""
    content = LandingPageContent.get_instance()
    photos = LandingPagePhoto.objects.all()
    contact_topics = LandingPageContactTopic.objects.all()
    form_links = LandingPageFormLink.objects.all()
    social_links = LandingPageSocialLink.objects.all()

    if request.method == 'POST':
        action = request.POST.get('action', 'save_content')

        if action == 'save_content':
            content.tagline = request.POST.get('tagline', content.tagline).strip()
            content.who_we_are_html = request.POST.get('who_we_are_html', '').strip()
            content.what_we_believe_html = request.POST.get('what_we_believe_html', '').strip()
            content.chapter_history_html = request.POST.get('chapter_history_html', '').strip()
            content.chapter_history_title = request.POST.get('chapter_history_title', content.chapter_history_title).strip()

            # SEO
            content.meta_description = request.POST.get('meta_description', '').strip()

            # Contact info
            content.contact_location = request.POST.get('contact_location', '').strip()
            content.contact_address  = request.POST.get('contact_address',  '').strip()
            content.contact_phone    = request.POST.get('contact_phone',    '').strip()

            # Section visibility
            content.show_parliament_info = 'show_parliament_info' in request.POST
            content.show_contact_section = 'show_contact_section' in request.POST

            # Recruitment banner
            content.recruitment_banner_active  = 'recruitment_banner_active' in request.POST
            content.recruitment_banner_message = request.POST.get('recruitment_banner_message', '').strip()
            end_date_str = request.POST.get('recruitment_banner_end', '').strip()
            if end_date_str:
                from datetime import date
                try:
                    content.recruitment_banner_end = date.fromisoformat(end_date_str)
                except ValueError:
                    content.recruitment_banner_end = None
            else:
                content.recruitment_banner_end = None

            # OG image (file upload, optional)
            og_image = request.FILES.get('og_image')
            if og_image:
                content.og_image = og_image

            content.updated_by = request.user
            content.save()
            logger.info(f"{request.user.username} updated landing page content")
            messages.success(request, 'Landing page content saved.')
            return redirect('edit_landing_page')

        elif action == 'upload_photo':
            image = request.FILES.get('image')
            if not image:
                messages.error(request, 'No image file provided.')
                return redirect('edit_landing_page')
            caption = request.POST.get('caption', '').strip()
            try:
                display_order = int(request.POST.get('display_order', 0))
            except (ValueError, TypeError):
                display_order = 0
            LandingPagePhoto.objects.create(
                image=image,
                caption=caption,
                display_order=display_order,
                uploaded_by=request.user,
            )
            logger.info(f"{request.user.username} uploaded landing page photo")
            messages.success(request, 'Photo uploaded.')
            return redirect('edit_landing_page')

        elif action == 'delete_photo':
            photo_id = request.POST.get('photo_id')
            try:
                photo = LandingPagePhoto.objects.get(pk=photo_id)
                photo.image.delete(save=False)
                photo.delete()
                logger.info(f"{request.user.username} deleted landing page photo {photo_id}")
                messages.success(request, 'Photo deleted.')
            except LandingPagePhoto.DoesNotExist:
                messages.error(request, 'Photo not found.')
            return redirect('edit_landing_page')

        elif action == 'reorder_photo':
            photo_id = request.POST.get('photo_id')
            try:
                new_order = int(request.POST.get('display_order', 0))
                LandingPagePhoto.objects.filter(pk=photo_id).update(display_order=new_order)
            except (ValueError, TypeError, LandingPagePhoto.DoesNotExist):
                pass
            return redirect('edit_landing_page')

        # ── Social Links ────────────────────────────────────────────────────────
        elif action == 'add_social_link':
            label = request.POST.get('social_label', '').strip()
            url   = request.POST.get('social_url', '').strip()
            if label and url:
                try:
                    order = int(request.POST.get('social_order', 0))
                except (ValueError, TypeError):
                    order = 0
                LandingPageSocialLink.objects.create(label=label, url=url, display_order=order)
                logger.info(f"{request.user.username} added social link '{label}'")
                messages.success(request, f'Link "{label}" added.')
            else:
                messages.error(request, 'Label and URL are required.')
            return redirect('edit_landing_page')

        elif action == 'delete_social_link':
            link_id = request.POST.get('link_id')
            try:
                link = LandingPageSocialLink.objects.get(pk=link_id)
                logger.info(f"{request.user.username} deleted social link '{link.label}'")
                link.delete()
                messages.success(request, 'Link deleted.')
            except LandingPageSocialLink.DoesNotExist:
                messages.error(request, 'Link not found.')
            return redirect('edit_landing_page')

        # ── Contact Topics ──────────────────────────────────────────────────────
        elif action == 'add_contact_topic':
            label = request.POST.get('topic_label', '').strip()
            role_code = request.POST.get('topic_role_code', '').strip()
            if label:
                try:
                    order = int(request.POST.get('topic_order', 0))
                except (ValueError, TypeError):
                    order = 0
                LandingPageContactTopic.objects.create(
                    label=label, role_code=role_code, display_order=order
                )
                logger.info(f"{request.user.username} added contact topic '{label}'")
                messages.success(request, f'Contact topic "{label}" added.')
            else:
                messages.error(request, 'Topic label is required.')
            return redirect('edit_landing_page')

        elif action == 'delete_contact_topic':
            topic_id = request.POST.get('topic_id')
            try:
                topic = LandingPageContactTopic.objects.get(pk=topic_id)
                logger.info(f"{request.user.username} deleted contact topic '{topic.label}'")
                topic.delete()
                messages.success(request, 'Contact topic deleted.')
            except LandingPageContactTopic.DoesNotExist:
                messages.error(request, 'Topic not found.')
            return redirect('edit_landing_page')

        elif action == 'toggle_contact_topic':
            topic_id = request.POST.get('topic_id')
            try:
                topic = LandingPageContactTopic.objects.get(pk=topic_id)
                topic.is_active = not topic.is_active
                topic.save(update_fields=['is_active'])
            except LandingPageContactTopic.DoesNotExist:
                pass
            return redirect('edit_landing_page')

        # ── Form Links ──────────────────────────────────────────────────────────
        elif action == 'add_form_link':
            title = request.POST.get('form_title', '').strip()
            url = request.POST.get('form_url', '').strip()
            if title and url:
                try:
                    order = int(request.POST.get('form_order', 0))
                except (ValueError, TypeError):
                    order = 0
                LandingPageFormLink.objects.create(
                    title=title,
                    description=request.POST.get('form_description', '').strip(),
                    url=url,
                    button_text=request.POST.get('form_button_text', 'Apply Now').strip() or 'Apply Now',
                    display_order=order,
                    created_by=request.user,
                )
                logger.info(f"{request.user.username} added form link '{title}'")
                messages.success(request, f'Form link "{title}" added.')
            else:
                messages.error(request, 'Title and URL are required.')
            return redirect('edit_landing_page')

        elif action == 'delete_form_link':
            link_id = request.POST.get('link_id')
            try:
                link = LandingPageFormLink.objects.get(pk=link_id)
                logger.info(f"{request.user.username} deleted form link '{link.title}'")
                link.delete()
                messages.success(request, 'Form link deleted.')
            except LandingPageFormLink.DoesNotExist:
                messages.error(request, 'Form link not found.')
            return redirect('edit_landing_page')

        elif action == 'toggle_form_link':
            link_id = request.POST.get('link_id')
            try:
                link = LandingPageFormLink.objects.get(pk=link_id)
                link.is_active = not link.is_active
                link.save(update_fields=['is_active'])
            except LandingPageFormLink.DoesNotExist:
                pass
            return redirect('edit_landing_page')

    return render(request, 'officer/edit_landing_page.html', {
        'content': content,
        'photos': photos,
        'contact_topics': contact_topics,
        'form_links': form_links,
        'social_links': social_links,
    })

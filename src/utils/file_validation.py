"""
Secure file upload validation

Validates file uploads to prevent:
- Malicious file uploads (executables, scripts)
- File type spoofing
- Oversized files
- Double extensions (.pdf.exe)

Usage:
    from src.utils.file_validation import validate_uploaded_file

    try:
        validate_uploaded_file(request.FILES['document'])
        # File is safe, proceed with upload
    except ValidationError as e:
        messages.error(request, str(e))
"""

import os
import magic
from django.core.exceptions import ValidationError
from django.conf import settings


# Maximum file size: 20MB (configurable in settings)
MAX_FILE_SIZE = getattr(settings, 'FILE_UPLOAD_MAX_MEMORY_SIZE', 20 * 1024 * 1024)

# Allowed file extensions and their MIME types
ALLOWED_FILE_TYPES = {
    # Documents
    '.pdf': ['application/pdf'],
    '.doc': ['application/msword'],
    '.docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
    '.odt': ['application/vnd.oasis.opendocument.text'],

    # Spreadsheets
    '.xls': ['application/vnd.ms-excel'],
    '.xlsx': ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
    '.ods': ['application/vnd.oasis.opendocument.spreadsheet'],
    '.csv': ['text/csv', 'text/plain', 'application/csv'],

    # Presentations
    '.ppt': ['application/vnd.ms-powerpoint'],
    '.pptx': ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
    '.odp': ['application/vnd.oasis.opendocument.presentation'],

    # Text files
    '.txt': ['text/plain'],
    '.md': ['text/plain', 'text/markdown'],
    '.rtf': ['application/rtf', 'text/rtf'],

    # Images (for reports, documents with photos)
    '.jpg': ['image/jpeg'],
    '.jpeg': ['image/jpeg'],
    '.png': ['image/png'],
    '.gif': ['image/gif'],
    '.webp': ['image/webp'],

    # Archives (for bundled documents)
    '.zip': ['application/zip', 'application/x-zip-compressed'],
}

# Dangerous extensions that should NEVER be allowed
BLOCKED_EXTENSIONS = {
    # Executables
    '.exe', '.dll', '.bat', '.cmd', '.com', '.msi', '.scr',
    # Scripts
    '.sh', '.bash', '.ps1', '.vbs', '.js', '.jar',
    # Web files that could be executed
    '.php', '.asp', '.aspx', '.jsp', '.cgi',
    # Other dangerous
    '.app', '.deb', '.rpm', '.dmg', '.pkg',
}


def get_file_extension(filename):
    """Get lowercase file extension including the dot"""
    return os.path.splitext(filename.lower())[1]


def check_double_extension(filename):
    """
    Check for double extensions like .pdf.exe
    Returns True if suspicious double extension detected
    """
    parts = filename.lower().split('.')
    if len(parts) > 2:
        # Check if any part before the last is a blocked extension
        for part in parts[:-1]:
            if f'.{part}' in BLOCKED_EXTENSIONS:
                return True
    return False


def validate_file_extension(filename):
    """Validate file extension is allowed"""
    ext = get_file_extension(filename)

    # Check for blocked extensions
    if ext in BLOCKED_EXTENSIONS:
        raise ValidationError(
            f'File type "{ext}" is not allowed for security reasons. '
            f'Please upload documents, spreadsheets, or presentations only.'
        )

    # Check for allowed extensions
    if ext not in ALLOWED_FILE_TYPES:
        allowed = ', '.join(sorted(ALLOWED_FILE_TYPES.keys()))
        raise ValidationError(
            f'File type "{ext}" is not allowed. '
            f'Allowed types: {allowed}'
        )

    # Check for double extensions
    if check_double_extension(filename):
        raise ValidationError(
            f'File "{filename}" has a suspicious double extension. '
            f'This is a common malware technique. Please rename the file.'
        )


def validate_mime_type(uploaded_file):
    """
    Validate MIME type matches the file extension
    Uses python-magic to detect actual file content
    """
    # Get the declared extension
    ext = get_file_extension(uploaded_file.name)
    allowed_mimes = ALLOWED_FILE_TYPES.get(ext, [])

    if not allowed_mimes:
        raise ValidationError(f'No MIME types defined for {ext}')

    # Get actual MIME type from file content
    try:
        # Read a chunk of the file to detect type
        chunk = uploaded_file.read(2048)
        uploaded_file.seek(0)  # Reset file pointer

        actual_mime = magic.from_buffer(chunk, mime=True)

        # Check if actual MIME matches allowed MIME types
        if actual_mime not in allowed_mimes:
            raise ValidationError(
                f'File content does not match extension. '
                f'File appears to be "{actual_mime}" but has extension "{ext}". '
                f'This could be a malicious file.'
            )

    except Exception as e:
        # If MIME detection fails, log but don't block
        # (python-magic might not be available on all systems)
        import logging
        logger = logging.getLogger('function_calls')
        logger.warning(f'MIME type validation failed for {uploaded_file.name}: {e}')


def validate_file_size(uploaded_file):
    """Validate file size is within limits"""
    if uploaded_file.size > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE / (1024 * 1024)
        actual_mb = uploaded_file.size / (1024 * 1024)
        raise ValidationError(
            f'File size ({actual_mb:.1f}MB) exceeds maximum allowed size ({max_mb:.0f}MB). '
            f'Please upload a smaller file.'
        )

    # Also reject empty files
    if uploaded_file.size == 0:
        raise ValidationError('Uploaded file is empty. Please select a valid file.')


def validate_filename(filename):
    """Validate filename doesn't contain suspicious characters"""
    # Check for path traversal attempts
    if '..' in filename or '/' in filename or '\\' in filename:
        raise ValidationError(
            'Filename contains invalid characters. '
            'Please rename the file without paths or special characters.'
        )

    # Check for null bytes (used in some attacks)
    if '\x00' in filename:
        raise ValidationError('Filename contains null bytes. This file may be malicious.')

    # Ensure filename isn't too long
    if len(filename) > 255:
        raise ValidationError('Filename is too long. Maximum 255 characters.')


def validate_uploaded_file(uploaded_file):
    """
    Comprehensive validation of uploaded file

    Args:
        uploaded_file: Django UploadedFile object (from request.FILES)

    Raises:
        ValidationError: If file fails any validation check

    Returns:
        True if file passes all validation
    """
    if not uploaded_file:
        raise ValidationError('No file was uploaded.')

    filename = uploaded_file.name

    # 1. Validate filename
    validate_filename(filename)

    # 2. Validate file extension
    validate_file_extension(filename)

    # 3. Validate file size
    validate_file_size(uploaded_file)

    # 4. Validate MIME type matches extension
    validate_mime_type(uploaded_file)

    return True


def get_safe_filename(filename):
    """
    Sanitize filename to prevent security issues

    - Removes path components
    - Removes special characters
    - Preserves extension
    - Ensures uniqueness with timestamp if needed
    """
    import re
    from django.utils.text import slugify

    # Get basename (remove any path)
    filename = os.path.basename(filename)

    # Split into name and extension
    name, ext = os.path.splitext(filename)

    # Slugify the name (makes it URL-safe)
    safe_name = slugify(name)

    # If slugify removed everything, use a default
    if not safe_name:
        safe_name = 'document'

    # Combine with extension
    return f"{safe_name}{ext.lower()}"


def validate_image_file(uploaded_file):
    """
    Special validation for image uploads
    More restrictive than document uploads
    """
    allowed_image_types = {
        '.jpg': ['image/jpeg'],
        '.jpeg': ['image/jpeg'],
        '.png': ['image/png'],
        '.gif': ['image/gif'],
        '.webp': ['image/webp'],
    }

    ext = get_file_extension(uploaded_file.name)

    if ext not in allowed_image_types:
        raise ValidationError(
            f'Only image files are allowed: .jpg, .jpeg, .png, .gif, .webp'
        )

    # Rest of validation is the same
    validate_uploaded_file(uploaded_file)

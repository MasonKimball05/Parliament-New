#!/usr/bin/env python3
"""
Security Audit Script for Parliament Django Application

Checks for common vulnerabilities:
- SQL Injection risks (raw SQL queries)
- XSS vulnerabilities (unescaped template output)
- Insecure file uploads
- Missing CSRF protection
- Hardcoded secrets

Usage: python3 security_audit.py
"""

import os
import re
from pathlib import Path

# ANSI color codes
RED = '\033[91m'
YELLOW = '\033[93m'
GREEN = '\033[92m'
BLUE = '\033[94m'
RESET = '\033[0m'

issues = []
warnings = []


def print_header(text):
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}{text.center(70)}{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")


def print_issue(severity, file_path, line_num, issue_type, details):
    """Print a security issue"""
    color = RED if severity == "HIGH" else YELLOW
    print(f"{color}[{severity}]{RESET} {file_path}:{line_num}")
    print(f"  Issue: {issue_type}")
    print(f"  Details: {details}\n")

    if severity == "HIGH":
        issues.append((file_path, line_num, issue_type, details))
    else:
        warnings.append((file_path, line_num, issue_type, details))


def check_sql_injection(file_path, content):
    """Check for potential SQL injection vulnerabilities"""
    lines = content.split('\n')

    # Pattern 1: Raw SQL queries with string formatting
    for i, line in enumerate(lines, 1):
        # Check for raw() with string formatting
        if re.search(r'\.raw\(["\'].*%s.*["\'].*\)', line):
            print_issue(
                "HIGH",
                file_path,
                i,
                "SQL Injection Risk",
                "Using string formatting in raw SQL query. Use parameterized queries."
            )

        # Check for execute() with string formatting
        if re.search(r'\.execute\([f"\'].*\{.*\}.*["\'].*\)', line) or \
           re.search(r'\.execute\(["\'].*%.*["\'].*%', line):
            print_issue(
                "HIGH",
                file_path,
                i,
                "SQL Injection Risk",
                "Using string formatting in SQL execute(). Use parameterized queries."
            )

        # Check for extra() with string interpolation
        if '.extra(' in line and ('where' in line.lower() or 'select' in line.lower()):
            if '%' in line or '{' in line:
                print_issue(
                    "MEDIUM",
                    file_path,
                    i,
                    "Potential SQL Injection",
                    "Using extra() with string interpolation. Consider using ORM methods."
                )


def check_xss_vulnerabilities(file_path, content):
    """Check for potential XSS vulnerabilities in templates"""
    if not file_path.endswith('.html'):
        return

    lines = content.split('\n')

    for i, line in enumerate(lines, 1):
        # Check for |safe filter usage
        if re.search(r'\{\{.*\|safe\}\}', line):
            print_issue(
                "MEDIUM",
                file_path,
                i,
                "XSS Risk",
                "Using |safe filter. Ensure content is properly sanitized."
            )

        # Check for autoescape off
        if re.search(r'\{%\s*autoescape\s+off\s*%\}', line):
            print_issue(
                "HIGH",
                file_path,
                i,
                "XSS Risk",
                "Autoescaping is disabled. This can lead to XSS vulnerabilities."
            )

        # Check for mark_safe usage
        if 'mark_safe' in line:
            print_issue(
                "MEDIUM",
                file_path,
                i,
                "XSS Risk",
                "Using mark_safe(). Ensure content is properly sanitized."
            )


def check_file_upload_security(file_path, content):
    """Check for insecure file upload handling"""
    lines = content.split('\n')

    for i, line in enumerate(lines, 1):
        # Check for file saves without validation
        if re.search(r'request\.FILES', line):
            # Look for validation in nearby lines
            context = '\n'.join(lines[max(0, i-5):min(len(lines), i+5)])

            if 'content_type' not in context.lower() and 'allowed' not in context.lower():
                print_issue(
                    "MEDIUM",
                    file_path,
                    i,
                    "Insecure File Upload",
                    "File upload without content type validation. Validate file types."
                )


def check_hardcoded_secrets(file_path, content):
    """Check for hardcoded secrets"""
    if file_path.endswith('.py'):
        lines = content.split('\n')

        for i, line in enumerate(lines, 1):
            # Skip comments
            if line.strip().startswith('#'):
                continue

            # Check for hardcoded SECRET_KEY
            if 'SECRET_KEY' in line and '=' in line and not 'os.getenv' in line:
                if not 'dev-only' in line.lower():
                    print_issue(
                        "HIGH",
                        file_path,
                        i,
                        "Hardcoded Secret",
                        "SECRET_KEY appears to be hardcoded. Use environment variables."
                    )

            # Check for hardcoded passwords
            if re.search(r'password\s*=\s*["\'](?!{{)[^"\']+["\']', line, re.IGNORECASE):
                if 'placeholder' not in line.lower() and 'example' not in line.lower():
                    print_issue(
                        "MEDIUM",
                        file_path,
                        i,
                        "Hardcoded Password",
                        "Password appears to be hardcoded. Use environment variables."
                    )


def check_csrf_protection(file_path, content):
    """Check for missing CSRF protection in forms"""
    if file_path.endswith('.html'):
        lines = content.split('\n')

        in_form = False
        has_csrf = False
        form_line = 0

        for i, line in enumerate(lines, 1):
            if '<form' in line.lower() and 'method=' in line.lower():
                if 'post' in line.lower():
                    in_form = True
                    form_line = i
                    has_csrf = False

            if in_form and 'csrf_token' in line:
                has_csrf = True

            if in_form and '</form>' in line.lower():
                if not has_csrf:
                    print_issue(
                        "HIGH",
                        file_path,
                        form_line,
                        "Missing CSRF Protection",
                        "POST form without {% csrf_token %}. Add CSRF protection."
                    )
                in_form = False


def scan_directory(directory):
    """Scan directory for security issues"""
    base_path = Path(directory)

    # Directories to scan
    scan_dirs = ['src', 'templates', 'Parliament']

    for scan_dir in scan_dirs:
        dir_path = base_path / scan_dir
        if not dir_path.exists():
            continue

        print(f"Scanning {scan_dir}/...")

        for file_path in dir_path.rglob('*'):
            if file_path.is_file():
                # Skip migration files, __pycache__, and other generated files
                if '__pycache__' in str(file_path) or 'migrations' in str(file_path):
                    continue

                if file_path.suffix in ['.py', '.html']:
                    try:
                        content = file_path.read_text()
                        relative_path = file_path.relative_to(base_path)

                        # Run checks
                        if file_path.suffix == '.py':
                            check_sql_injection(str(relative_path), content)
                            check_file_upload_security(str(relative_path), content)
                            check_hardcoded_secrets(str(relative_path), content)

                        if file_path.suffix == '.html':
                            check_xss_vulnerabilities(str(relative_path), content)
                            check_csrf_protection(str(relative_path), content)

                    except Exception as e:
                        print(f"Error reading {file_path}: {e}")


def print_summary():
    """Print summary of findings"""
    print_header("SECURITY AUDIT SUMMARY")

    print(f"{RED}High Severity Issues: {len(issues)}{RESET}")
    print(f"{YELLOW}Medium Severity Warnings: {len(warnings)}{RESET}\n")

    if len(issues) == 0 and len(warnings) == 0:
        print(f"{GREEN}✓ No security issues found!{RESET}")
        print(f"{GREEN}  Your code follows Django security best practices.{RESET}\n")
    else:
        if len(issues) > 0:
            print(f"{RED}⚠ Critical issues found that should be fixed immediately.{RESET}\n")
        if len(warnings) > 0:
            print(f"{YELLOW}! Warnings found that should be reviewed.{RESET}\n")

    print("\nRecommendations:")
    print("1. Always use Django ORM instead of raw SQL queries")
    print("2. Never use |safe or mark_safe without sanitizing user input")
    print("3. Always include {% csrf_token %} in POST forms")
    print("4. Validate file uploads (type, size, content)")
    print("5. Store secrets in environment variables, never in code")
    print("6. Keep Django and dependencies up to date")
    print("\nFor more information, see: https://docs.djangoproject.com/en/stable/topics/security/")


if __name__ == '__main__':
    print_header("PARLIAMENT SECURITY AUDIT")
    print("Checking for common security vulnerabilities...\n")

    # Get the base directory
    base_dir = Path(__file__).parent

    # Run the scan
    scan_directory(base_dir)

    # Print summary
    print_summary()

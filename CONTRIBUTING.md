# Contributing to Parliament

Thank you for your interest in contributing to Parliament! This document provides guidelines and instructions for contributing to this project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Code Style Guidelines](#code-style-guidelines)
- [Commit Message Guidelines](#commit-message-guidelines)
- [Pull Request Process](#pull-request-process)
- [Testing](#testing)
- [Reporting Bugs](#reporting-bugs)
- [Feature Requests](#feature-requests)
- [Questions](#questions)

---

## Code of Conduct

By participating in this project, you agree to maintain a respectful environment. We expect all contributors to:

- Be respectful and considerate in communication
- Accept constructive criticism
- Focus on what is best for the project 
- Show empathy towards other contributors

---

## Getting Started

### Prerequisites

Before contributing, ensure you have:

- Python 3.13 or higher
- PostgreSQL 15 or higher
- Redis 7 or higher
- Git
- A GitHub account

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR-USERNAME/Parliament.git
   cd Parliament
   ```
3. Add the upstream repository:
   ```bash
   git remote add upstream https://github.com/MasonKimball05/Parliament.git
   ```

---

## Development Setup

### 1. Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

```bash
cp .env.example .env
# Edit .env with your local database credentials
```

Required environment variables:
- `DJANGO_SECRET_KEY` - Generate with: `python manage.py shell -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
- `DB_NAME` - Your PostgreSQL database name
- `DB_USER` - Database username
- `DB_PASSWORD` - Database password
- `DB_HOST` - Usually `localhost` for development
- `DB_PORT` - Usually `5432`
- `REDIS_URL` - Usually `redis://localhost:6379/0` for development
- `ENCRYPTION_KEY` - Fernet key for field-level encryption

### 4. Set Up Database

```bash
# Create the database (in psql)
createdb parliament_db

# Run migrations
python manage.py migrate

# Restore default committees and roles
python manage.py restore_committees_and_roles

# Create a superuser for testing
python manage.py createsuperuser
```

### 5. Run the Development Server

```bash
python manage.py runserver
```

For WebSocket/chat support in development, use Daphne instead:
```bash
daphne -p 8000 Parliament.asgi:application
```

Visit `http://localhost:8000` to verify your setup.

---

## Code Style Guidelines

### Python Code

- Follow [PEP 8](https://peps.python.org/pep-0008/) style guidelines
- Use 4 spaces for indentation (no tabs)
- Maximum line length: 120 characters
- Use descriptive variable and function names
- Add docstrings to functions and classes

#### Example

```python
def calculate_vote_result(legislation, threshold=0.51):
    """
    Calculate whether legislation has passed based on votes.

    Args:
        legislation: The Legislation model instance
        threshold: Percentage of yes votes required to pass (default: 51%)

    Returns:
        tuple: (passed: bool, yes_percentage: float)
    """
    total_votes = legislation.yes_votes + legislation.no_votes
    if total_votes == 0:
        return False, 0.0

    yes_percentage = legislation.yes_votes / total_votes
    return yes_percentage >= threshold, yes_percentage
```

### Django-Specific Guidelines

- Use class-based views when appropriate, function-based views for simple operations
- Keep views thin, move business logic to models or utility functions
- Use Django's ORM properly - avoid raw SQL unless necessary
- Use `select_related()` and `prefetch_related()` to optimize queries
- Always validate user input and use Django forms

### Templates

- Use semantic HTML5 elements
- Follow existing Tailwind CSS patterns in the codebase
- Keep templates DRY by using template inheritance and includes
- Use Django template tags appropriately

### JavaScript

- Use vanilla JavaScript (no jQuery dependency)
- Follow existing patterns in the codebase
- Keep JavaScript minimal and progressive enhancement focused

---

## Commit Message Guidelines

We follow the conventional commit format for clear and consistent commit history.

### Format

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types

- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, missing semicolons, etc.)
- `refactor`: Code changes that neither fix bugs nor add features
- `test`: Adding or updating tests
- `chore`: Maintenance tasks (build process, dependencies, etc.)
- `perf`: Performance improvements

### Scope (Optional)

The scope should indicate the component affected:
- `auth`: Authentication related
- `vote`: Voting system
- `committee`: Committee management
- `models`: Database models
- `views`: View functions
- `templates`: HTML templates
- `admin`: Admin functionality
- `api`: API endpoints

### Examples

```
feat(vote): add plurality voting mode

fix(committee): prevent duplicate member assignments

docs: update installation instructions in README

test(auth): add login validation tests

refactor(models): optimize legislation query performance
```

### Guidelines

- Use present tense ("add feature" not "added feature")
- Use imperative mood ("move cursor" not "moves cursor")
- Keep the first line under 72 characters
- Reference issues in the footer when applicable: `Fixes #123`

---

## Pull Request Process

### 1. Create a Feature Branch

```bash
git checkout main
git pull upstream main
git checkout -b feature/your-feature-name
```

Branch naming conventions:
- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation updates
- `refactor/` - Code refactoring
- `test/` - Test additions or updates

### 2. Make Your Changes

- Follow the code style guidelines above
- Keep changes focused and atomic
- Update documentation if needed
- Add or update tests as appropriate

### 3. Test Your Changes

```bash
# Run the full test suite
python manage.py test

# Run specific tests
python manage.py test src.test_comprehensive

# Check for migrations
python manage.py makemigrations --check --dry-run
```

### 4. Commit Your Changes

```bash
git add .
git commit -m "feat(scope): description of changes"
```

### 5. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub with:
- Clear title following commit message format
- Description of what changes were made and why
- Reference to any related issues
- Screenshots for UI changes

### 6. PR Review

- Respond to review feedback promptly
- Make requested changes in new commits
- Request re-review when ready
- Squash commits before merge if requested

### PR Checklist

Before submitting, ensure:
- [ ] All tests pass locally
- [ ] New code has appropriate test coverage
- [ ] Documentation is updated if needed
- [ ] No merge conflicts with main branch
- [ ] Commit messages follow guidelines
- [ ] No sensitive information (passwords, keys) in code

---

## Testing

### Running Tests

```bash
# Run all tests
python manage.py test

# Run with verbosity
python manage.py test --verbosity=2

# Run specific test file
python manage.py test src.test_comprehensive

# Run specific test class
python manage.py test src.test_comprehensive.VoteModeTestCase

# Run specific test method
python manage.py test src.test_comprehensive.VoteModeTestCase.test_percentage_mode_pass
```

### Writing Tests

- Place tests in `src/tests.py` or create new test files following the `test_*.py` pattern
- Use Django's `TestCase` for database tests
- Test both success and failure cases
- Test edge cases and boundary conditions
- Mock external services when necessary

### Test Structure

```python
from django.test import TestCase, Client
from src.models import ParliamentUser, Legislation

class LegislationTestCase(TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.client = Client()
        self.user = ParliamentUser.objects.create_user(
            user_id='test123',
            name='Test User',
            username='testuser'
        )
        self.user.set_password('testpass')
        self.user.save()

    def test_create_legislation(self):
        """Test that legislation can be created successfully."""
        # Arrange
        data = {
            'title': 'Test Legislation',
            'vote_mode': 'percentage'
        }

        # Act
        self.client.login(username='testuser', password='testpass')
        response = self.client.post('/vote/upload/', data)

        # Assert
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Legislation.objects.filter(title='Test Legislation').exists())

    def tearDown(self):
        """Clean up after tests."""
        pass
```

### Coverage

```bash
pip install coverage
coverage run --source='.' manage.py test
coverage report
coverage html  # View detailed report at htmlcov/index.html
```

Aim for at least 80% code coverage for new features.

---

## Reporting Bugs

### Before Reporting

1. Check existing [GitHub Issues](https://github.com/MasonKimball05/Parliament/issues) to avoid duplicates
2. Try to reproduce the issue with the latest version
3. Gather relevant information (steps to reproduce, error messages, logs)

### Bug Report Template

When creating a bug report, include:

```markdown
**Description**
A clear description of the bug.

**Steps to Reproduce**
1. Go to '...'
2. Click on '...'
3. See error

**Expected Behavior**
What you expected to happen.

**Actual Behavior**
What actually happened.

**Screenshots**
If applicable, add screenshots.

**Environment**
- OS: [e.g., macOS 14, Ubuntu 22.04]
- Browser: [e.g., Chrome 120, Firefox 121]
- Python Version: [e.g., 3.11.5]
- Django Version: [e.g., 4.2.7]

**Additional Context**
Any other relevant information.
```

---

## Feature Requests

### Before Requesting

1. Check the [Roadmap](README.md#-roadmap) for planned features
2. Search existing issues for similar requests
3. Consider if the feature aligns with the project's goals

### Feature Request Template

```markdown
**Is your feature request related to a problem?**
A clear description of the problem. Ex. "I'm always frustrated when..."

**Describe the solution you'd like**
A clear description of what you want to happen.

**Describe alternatives you've considered**
Any alternative solutions or features you've considered.

**Additional context**
Any other context, mockups, or examples.
```

---

## Questions

If you have questions about contributing:

- Check the [README](README.md) and existing documentation
- Search [existing issues](https://github.com/MasonKimball05/Parliament/issues)
- Open a [Discussion](https://github.com/MasonKimball05/Parliament/discussions)
- Email: mason.kimball@icloud.com

---

## Recognition

Contributors will be recognized in:
- The GitHub contributors list
- Release notes for significant contributions

Thank you for contributing to Parliament!

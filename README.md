# 🏛️ Parliament - Chapter Management System

A comprehensive Django-based management system for student organizations, designed to streamline legislation, voting, committee management, and chapter operations.

[![Django CI/CD](https://github.com/MasonKimball05/Parliament/workflows/Django%20CI%2FCD/badge.svg)](https://github.com/MasonKimball05/Parliament/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Django 4.2+](https://img.shields.io/badge/django-4.2+-green.svg)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [Features](#-features)
- [Quick Start](#-quick-start)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Usage](#-usage)
- [Testing](#-testing)
- [Deployment](#-deployment)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

### Legislation & Voting
- **Multiple Vote Modes**
  - **Percentage**: Traditional yes/no/abstain voting with configurable thresholds (51%, 60%, 67%, 75%, unanimous)
  - **Piecewise**: Requires exact number of yes votes to pass
  - **Plurality**: Multiple choice voting with single winner determination
- **Anonymous Voting**: Optional anonymous ballot mode
- **Real-time Vote Tallies**: Live vote counts for legislation authors
- **Vote History**: Comprehensive tracking of all past legislation
- **Automated Vote Calculations**: Smart pass/fail determination based on mode

### Committee Management
- **Committee Structure**: Pre-configured with default committees (Brotherhood, Finance, Education, etc.)
- **Committee Legislation**: Separate voting system for committee-level decisions
- **Document Management**: Upload and share minutes, agendas, reports, and policies
- **Member Roles**: Chairs, members, voting members, and advisors
- **Committee-to-Chapter**: Push approved committee legislation to chapter-wide votes

### User & Profile Management
- **Preferred Names**: Optional preferred first name display (e.g., "Mike Johnson" instead of "Michael Johnson")
- **Role-Based Access**: Member, Chair, Officer, and Admin permissions
- **User Profiles**: Customizable profiles with contact info and roles
- **VP Positions**: Hard-coded executive roles (President, EVP, VPs of various areas)

### Events & Calendar
- **Event Management**: Create and manage chapter events with dates, times, and locations
- **Calendar View**: Visual calendar with all upcoming events
- **Automatic Archiving**: Events older than 1 year automatically archived
- **Manual Controls**: Admin ability to archive/unarchive events

### Attendance Tracking
- **Session Attendance**: Mark members present/absent for meetings
- **Voting Eligibility**: Only present members can vote (3-hour window)
- **Historical Records**: Complete attendance history

### Document Management
- **Chapter Documents**: Upload constitutions, bylaws, and policies
- **Committee Documents**: Committee-specific document repositories
- **Published/Unpublished**: Control visibility of documents to chapter
- **Document Types**: Minutes, agendas, reports, policies, general documents

### Admin Features
- **Officer Portal**: Dedicated dashboard for officers and chairs
- **Activity Logs**: Track all system actions and changes
- **Database Backup**: Built-in backup and restore functionality
- **User Management**: Create, modify, and manage member accounts
- **Announcements**: Post chapter-wide announcements

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL 13+
- pip and virtualenv

### Installation (5 minutes)

```bash
# 1. Clone the repository
git clone https://github.com/MasonKimball05/Parliament.git
cd Parliament

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env with your settings

# 5. Run migrations
python manage.py migrate

# 6. Restore default committees and roles
python manage.py restore_committees_and_roles

# 7. Create superuser
python manage.py createsuperuser

# 8. Run the development server
python manage.py runserver
```

Visit `http://localhost:8000` to see your application!

---

## 📦 Installation

### Manual Installation

#### 1. System Requirements
```bash
# macOS
brew install postgresql python3

# Ubuntu/Debian
sudo apt update
sudo apt install postgresql postgresql-contrib python3 python3-pip python3-venv

# Verify installations
python3 --version  # Should be 3.11+
psql --version     # Should be 13+
```

#### 2. Database Setup
```bash
# Start PostgreSQL
# macOS:
brew services start postgresql

# Ubuntu:
sudo systemctl start postgresql

# Create database and user
psql postgres
```

```sql
CREATE DATABASE parliament_db;
CREATE USER parliament_user WITH PASSWORD 'your_password';
ALTER ROLE parliament_user SET client_encoding TO 'utf8';
ALTER ROLE parliament_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE parliament_user SET timezone TO 'America/Chicago';
GRANT ALL PRIVILEGES ON DATABASE parliament_db TO parliament_user;
\q
```

#### 3. Application Setup
```bash
# Clone repository
git clone https://github.com/MasonKimball05/Parliament.git
cd Parliament

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env file with your database credentials and secret key

# Run migrations
python manage.py migrate

# Restore default data
python manage.py restore_committees_and_roles

# Collect static files
python manage.py collectstatic --noinput

# Create admin account
python manage.py createsuperuser
```

---

## ⚙️ Configuration

### Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
# Generate a secure secret key
python manage.py shell -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Key settings:
- `SECRET_KEY`: Django secret key (keep this secure!)
- `DEBUG`: Set to `False` in production
- `ALLOWED_HOSTS`: Your domain names
- `DB_*`: Database connection settings
- `TIME_ZONE`: Your local timezone

### Default Data

The system includes 11 pre-configured committees:
1. Constitution and Bylaws Committee
2. Ritual Committee
3. Executive Board
4. Kai
5. Brotherhood
6. Recruitment
7. Education
8. Risk Management
9. Finance
10. Administration
11. Programming

And 9 VP roles:
1. President
2. Executive Vice President
3. VP of Brotherhood
4. VP of Risk Management
5. VP of Education
6. VP of Recruitment
7. VP of Programming
8. VP of Finance
9. VP of Administration

Restore these after any database reset:
```bash
python manage.py restore_committees_and_roles
```

---

## 💻 Usage

### Creating Users

```bash
# Via Django admin
python manage.py createsuperuser
# Then visit http://localhost:8000/admin

# Or via management command
python manage.py shell
```

```python
from src.models import ParliamentUser

user = ParliamentUser.objects.create_user(
    user_id='12345',
    name='John Doe',
    username='jdoe',
    member_type='Member'
)
user.set_password('password')
user.save()
```

### Creating Legislation

1. Log in as Officer or Chair
2. Go to `/vote/`
3. Fill out the "Upload New Legislation" form
4. Select vote mode:
   - **Percentage**: Yes/No vote with threshold
   - **Piecewise**: Requires exact number of yes votes
   - **Plurality**: Multiple choice vote
5. Set availability time
6. Upload document (PDF/DOCX)
7. Submit

### Voting on Legislation

1. Members must be marked present (within 3-hour window)
2. Go to `/vote/`
3. Select vote choice
4. Enter password to confirm
5. Submit vote

### Managing Committees

1. Navigate to `/committees/`
2. Select a committee
3. Chairs can:
   - Upload documents
   - Create committee votes
   - Manage members
   - View minutes
   - Push legislation to chapter

### Archiving Old Events

Automatically archive events older than 1 year:
```bash
python manage.py archive_old_events
```

Or manually archive via Admin portal:
1. Go to `/officers/events/`
2. Click archive icon next to event
3. View archived events at `/officers/archived-events/`

---

## 🧪 Testing

### Running Tests

```bash
# Run all tests
python manage.py test

# Run specific test file
python manage.py test src.test_comprehensive

# Run specific test class
python manage.py test src.test_comprehensive.VoteModeTestCase

# Run with verbosity
python manage.py test --verbosity=2

# Run with coverage
pip install coverage
coverage run --source='.' manage.py test
coverage report
coverage html  # Creates htmlcov/index.html
```

### Test Files

- `src/tests.py` - Original test suite
- `src/test_comprehensive.py` - Comprehensive tests (50+ tests)
- `src/test_edge_cases.py` - Edge cases and integration tests (20+ tests)

See [TESTING.md](TESTING.md) for detailed testing documentation.

---

## 🚢 Deployment

### Docker Deployment

```bash
# Build and run with Docker Compose
docker-compose up -d

# Run migrations
docker-compose exec web python manage.py migrate

# Create superuser
docker-compose exec web python manage.py createsuperuser

# View logs
docker-compose logs -f web
```

### Production Deployment

#### Prerequisites
- Server with Ubuntu 20.04+ or similar
- Domain name pointing to your server
- SSL certificate (Let's Encrypt recommended)

#### Quick Deploy with Docker

```bash
# 1. Clone repository on server
git clone https://github.com/MasonKimball05/Parliament.git
cd Parliament

# 2. Configure environment
cp .env.example .env
nano .env  # Edit with production settings
# Set DEBUG=False, add your domain to ALLOWED_HOSTS

# 3. Build and start services
docker-compose -f docker-compose.prod.yml up -d

# 4. Run migrations
docker-compose exec web python manage.py migrate

# 5. Restore default data
docker-compose exec web python manage.py restore_committees_and_roles

# 6. Create admin account
docker-compose exec web python manage.py createsuperuser

# 7. Collect static files
docker-compose exec web python manage.py collectstatic --noinput
```

#### Manual Production Setup

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed deployment instructions including:
- Gunicorn configuration
- Nginx setup
- SSL certificates
- Database backups
- Monitoring and logging

---

## 🔧 Management Commands

### Custom Commands

```bash
# Restore committees and VP roles
python manage.py restore_committees_and_roles

# Archive events older than 1 year
python manage.py archive_old_events

# Archive only (dry run)
python manage.py archive_old_events --dry-run

# Database backup
python manage.py dumpdata > backup.json

# Clear expired attendance records
python manage.py clear_expired_attendance

# Clean up old legislation
python manage.py cleanup_legislation
```

---

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Quick Contribution Guide

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Write/update tests
5. Run the test suite (`python manage.py test`)
6. Commit your changes (`git commit -m 'Add amazing feature'`)
7. Push to your branch (`git push origin feature/amazing-feature`)
8. Open a Pull Request

---

## 📝 Project Structure

```
Parliament/
├── .github/
│   └── workflows/
│       └── ci.yml              # GitHub Actions CI/CD
├── src/
│   ├── management/
│   │   └── commands/           # Custom management commands
│   ├── templatetags/           # Custom template filters
│   ├── view/                   # View modules
│   ├── models.py               # Database models
│   ├── forms.py                # Django forms
│   ├── urls.py                 # URL routing
│   ├── tests.py                # Original tests
│   ├── test_comprehensive.py   # Comprehensive test suite
│   └── test_edge_cases.py      # Edge case tests
├── templates/                  # HTML templates
├── static/                     # Static files (CSS, JS, images)
├── media/                      # User-uploaded files
├── Parliament/                 # Project settings
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── Dockerfile                  # Docker configuration
├── docker-compose.yml          # Docker Compose config
├── nginx.conf                  # Nginx configuration
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variables template
├── manage.py                   # Django management script
├── README.md                   # This file
├── TESTING.md                  # Testing documentation
└── DEPLOYMENT.md               # Deployment guide
```

---

## 📖 Documentation

- [Testing Guide](TESTING.md) - Comprehensive testing documentation
- [Test Examples](TEST_EXAMPLES.md) - Quick reference for writing tests
- [Deployment Guide](DEPLOYMENT.md) - Production deployment instructions
- [Contributing Guidelines](CONTRIBUTING.md) - How to contribute

---

## 🐛 Troubleshooting

### Common Issues

**Database Connection Error**
```bash
# Check PostgreSQL is running
pg_isready

# Check credentials in .env file
# Verify database exists
psql -U parliament_user -d parliament_db
```

**Migration Errors**
```bash
# Reset migrations (DESTRUCTIVE)
python manage.py migrate --fake src zero
python manage.py migrate

# Or reset database completely
dropdb parliament_db
createdb parliament_db
python manage.py migrate
```

**Static Files Not Loading**
```bash
# Collect static files
python manage.py collectstatic --clear --noinput

# Check STATIC_ROOT in settings
# Verify DEBUG=True for development
```

**Permission Errors**
```bash
# Ensure proper permissions on media directory
chmod -R 755 media/
chown -R $USER:$USER media/
```

---

## 📊 Tech Stack

- **Backend**: Django 4.2+
- **Database**: PostgreSQL 15
- **Frontend**: HTML, Tailwind CSS, JavaScript
- **Authentication**: Django Auth
- **File Storage**: Django FileField (local/S3)
- **Testing**: Django TestCase, Coverage
- **CI/CD**: GitHub Actions
- **Deployment**: Docker, Gunicorn, Nginx

---

## 🔒 Security

- CSRF protection enabled
- XSS protection enabled
- SQL injection protection via ORM
- Password hashing with Django's PBKDF2
- Secure session cookies in production
- File upload validation
- Permission-based access control

Report security vulnerabilities to: mason.kimball@icloud.com

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 👥 Authors

- **Mason Kimball** - [MasonKimball05](https://github.com/MasonKimball05)

See also the list of [contributors](https://github.com/yourusername/Parliament/contributors) who participated in this project.

---

## 🙏 Acknowledgments

- Built for student organizations to streamline chapter operations
- Inspired by parliamentary procedure and democratic governance
- Thanks to all contributors and testers

---

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/Parliament/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/Parliament/discussions)
- **Email**: mason.kimball@icloud.com

---

## 🗺️ Roadmap

- [ ] Email notifications for new legislation
- [ ] Mobile app (React Native)
- [ ] Advanced analytics dashboard
- [ ] Automated report generation
- [ ] Calendar integrations (Google Calendar, Outlook)
- [ ] SMS reminders for events

---

**Made with ❤️ for student organizations**

# Parliament Testing Guide

This document explains the test suite for the Parliament system and how to run tests.

## Test Files

### 1. `src/tests.py` (Original Tests)
Contains basic tests for:
- End vote functionality
- Vote tally accuracy
- Anonymous voting behavior
- Comprehensive voting scenarios
- Piecewise voting mode
- Plurality voting mode

### 2. `src/test_comprehensive.py` (Comprehensive Tests)
Extensive tests covering:
- **All three vote modes** (percentage, piecewise, plurality)
- **Legislation views** (passed legislation, legislation history)
- **User profiles** (preferred name, username updates)
- **Committee functionality** (permissions, legislation)
- **Event archiving** (manual archive/unarchive)
- **Model methods** (display names, vote calculations)
- **Permissions** (admin, officer, member access)

### 3. `src/test_edge_cases.py` (Edge Cases & Integration)
Tests for:
- **Edge cases** (zero votes, all abstains, ties)
- **Integration tests** (full workflows)
- **Attendance requirements** for voting
- **Display name logic** (various name formats)
- **Event archiving edge cases**
- **Stress tests** (100+ voters, 10+ options)
- **Data integrity** (unique constraints)

## Running Tests

### Run All Tests
```bash
python manage.py test
```

### Run Specific Test File
```bash
# Original tests
python manage.py test src.tests

# Comprehensive tests
python manage.py test src.test_comprehensive

# Edge case tests
python manage.py test src.test_edge_cases
```

### Run Specific Test Class
```bash
# Run vote mode tests
python manage.py test src.test_comprehensive.VoteModeTestCase

# Run profile tests
python manage.py test src.test_comprehensive.ProfileTestCase

# Run edge case tests
python manage.py test src.test_edge_cases.EdgeCaseVotingTestCase
```

### Run Single Test Method
```bash
# Test percentage mode passes
python manage.py test src.test_comprehensive.VoteModeTestCase.test_percentage_mode_passes

# Test preferred name update
python manage.py test src.test_comprehensive.ProfileTestCase.test_preferred_name_update
```

### Run with Verbosity
```bash
# More detailed output
python manage.py test --verbosity=2

# Maximum detail
python manage.py test --verbosity=3
```

### Run Specific Tests with Pattern
```bash
# Run all vote-related tests
python manage.py test --pattern="*vote*"
```

## Test Coverage

### Vote Modes (11 tests)
- ✅ Percentage mode passes with >51%
- ✅ Percentage mode fails with <51%
- ✅ Piecewise mode passes with exact votes
- ✅ Piecewise mode fails with insufficient votes
- ✅ Plurality mode passes with clear winner
- ✅ Plurality mode fails with tie
- ✅ Zero votes handling
- ✅ All abstains handling
- ✅ Perfect 3-way tie
- ✅ 100 voters stress test
- ✅ 10 plurality options stress test

### Legislation Views (3 tests)
- ✅ Passed legislation page loads
- ✅ Legislation history page loads
- ✅ History shows only user's legislation

### User Profiles (6 tests)
- ✅ Profile page loads
- ✅ Preferred name update
- ✅ Preferred name clear
- ✅ Username update
- ✅ Display name without preferred
- ✅ Display name with preferred
- ✅ Hyphenated names
- ✅ Single names

### Committees (4 tests)
- ✅ is_chair method
- ✅ is_member method
- ✅ Committee detail view
- ✅ Committee legislation creation

### Events (4 tests)
- ✅ Manual archive event
- ✅ Manual unarchive event
- ✅ Archived events view
- ✅ Archive future event
- ✅ Double archive

### Permissions (4 tests)
- ✅ Admin can access officer home
- ✅ Officer can access officer home
- ✅ Member cannot access officer home
- ✅ Admin can archive events

### Model Methods (4 tests)
- ✅ is_available with past date
- ✅ is_available with future date
- ✅ required_yes_votes for piecewise
- ✅ required_yes_votes for percentage
- ✅ User __str__ method

### Integration Tests (2 tests)
- ✅ Full committee vote workflow
- ✅ Chapter vs committee legislation separation

### Attendance (2 tests)
- ✅ Present member can vote
- ✅ Absent member cannot vote

### Data Integrity (2 tests)
- ✅ Unique username constraint
- ✅ Unique committee code constraint

## Expected Test Results

All tests should pass. If you see failures:

1. **Migration Errors**: Run `python manage.py migrate` first
2. **Database Errors**: Ensure PostgreSQL is running
3. **Permission Errors**: Check user roles in test setup

## Writing New Tests

### Basic Test Structure
```python
from django.test import TestCase
from src.models import Legislation, ParliamentUser

class MyTestCase(TestCase):
    def setUp(self):
        """Create test data"""
        self.user = ParliamentUser.objects.create_user(
            user_id='test1',
            name='Test User',
            username='test',
            member_type='Member'
        )

    def test_something(self):
        """Test description"""
        # Create test objects
        # Perform actions
        # Assert expected results
        self.assertTrue(condition)
```

### Testing Vote Modes

**Percentage Mode:**
```python
leg = Legislation.objects.create(
    title='Test',
    posted_by=self.user,
    available_at=timezone.now(),
    vote_mode='percentage',
    required_percentage='51',
    document='test.pdf'
)
```

**Piecewise Mode:**
```python
leg = Legislation.objects.create(
    title='Test',
    posted_by=self.user,
    available_at=timezone.now(),
    vote_mode='piecewise',
    required_number=5,  # Needs exactly 5 yes votes
    document='test.pdf'
)
```

**Plurality Mode:**
```python
leg = Legislation.objects.create(
    title='Test',
    posted_by=self.user,
    available_at=timezone.now(),
    vote_mode='plurality',
    plurality_options=['Pizza', 'Burgers', 'Tacos']
)
```

### Testing Views

```python
def test_my_view(self):
    self.client.force_login(self.user)
    response = self.client.get(reverse('view_name'))

    self.assertEqual(response.status_code, 200)
    self.assertTemplateUsed(response, 'template.html')
    self.assertIn('key', response.context)
```

### Testing Forms

```python
def test_profile_update(self):
    self.client.force_login(self.user)
    response = self.client.post(reverse('profile'), {
        'profile_submit': '1',
        'username': 'newusername',
        'preferred_name': 'Nick'
    })

    self.user.refresh_from_db()
    self.assertEqual(self.user.preferred_name, 'Nick')
```

## Continuous Integration

Add to CI pipeline:
```yaml
# .github/workflows/tests.yml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: 3.x
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: python manage.py test --verbosity=2
```

## Test Database

Tests use a separate test database (`test_parliament_db`) that is:
- Created automatically before tests run
- Destroyed automatically after tests complete
- Completely isolated from your development database

## Best Practices

1. **Always use setUp()**: Create test data in setUp() method
2. **Test one thing**: Each test should verify one specific behavior
3. **Use descriptive names**: `test_percentage_passes_with_60_percent` not `test_vote`
4. **Clean up**: Tests should not affect each other
5. **Test edge cases**: Zero votes, ties, boundary conditions
6. **Test permissions**: Verify who can/cannot access features
7. **Test both success and failure**: Pass and fail conditions

## Debugging Failed Tests

### View Test Output
```bash
python manage.py test --verbosity=3
```

### Run Single Failed Test
```bash
python manage.py test src.test_comprehensive.VoteModeTestCase.test_percentage_mode_passes --pdb
```

### Check Test Database
```python
# In test method
from django.db import connection
print(connection.settings_dict['NAME'])  # Shows test DB name
```

### Print Debug Info
```python
def test_something(self):
    print(f"Debug: {variable}")  # Shows in test output
    import pdb; pdb.set_trace()  # Debugger breakpoint
```

## Coverage Report (Optional)

Install coverage:
```bash
pip install coverage
```

Run with coverage:
```bash
coverage run --source='.' manage.py test
coverage report
coverage html  # Creates htmlcov/index.html
```

## Summary

- **Total Tests**: 50+ comprehensive tests
- **Coverage Areas**: Votes, Legislation, Profiles, Committees, Events, Permissions
- **Test Types**: Unit, Integration, Edge Cases, Stress Tests
- **Execution Time**: ~10-30 seconds for full suite

Keep tests updated as new features are added!

# Test Examples & Quick Reference

Quick reference for writing tests in the Parliament system.

## Common Test Patterns

### Creating Test Users

```python
# Basic member
member = ParliamentUser.objects.create_user(
    user_id='mem1',
    name='John Doe',
    username='jdoe',
    member_type='Member'
)

# Officer with password
officer = ParliamentUser.objects.create_user(
    user_id='off1',
    name='Jane Smith',
    username='jsmith',
    member_type='Officer'
)
officer.set_password('testpass')
officer.save()

# Admin user
admin = ParliamentUser.objects.create_user(
    user_id='adm1',
    name='Admin User',
    username='admin',
    member_type='Officer'
)
admin.is_admin = True
admin.save()
```

### Creating Test Legislation

```python
# Percentage mode (default)
leg = Legislation.objects.create(
    title='Test Bill',
    description='Test description',
    posted_by=self.user,
    available_at=timezone.now(),
    vote_mode='percentage',
    required_percentage='51',
    document='test.pdf'
)

# Piecewise mode
leg = Legislation.objects.create(
    title='Piecewise Bill',
    description='Needs exact number of votes',
    posted_by=self.user,
    available_at=timezone.now(),
    vote_mode='piecewise',
    required_number=10,  # Needs 10 yes votes
    document='test.pdf'
)

# Plurality mode
leg = Legislation.objects.create(
    title='Plurality Bill',
    description='Multiple choice vote',
    posted_by=self.user,
    available_at=timezone.now(),
    vote_mode='plurality',
    plurality_options=['Option A', 'Option B', 'Option C']
)

# With additional options
leg = Legislation.objects.create(
    title='Custom Bill',
    description='Custom settings',
    posted_by=self.user,
    available_at=timezone.now() - timedelta(days=1),  # Available in past
    voting_closed=False,
    anonymous_vote=True,  # Anonymous
    allow_abstain=False,  # No abstain option
    vote_mode='percentage',
    required_percentage='67',  # 67% required
    document='test.pdf'
)
```

### Creating Votes

```python
# Simple yes vote
Vote.objects.create(
    user=voter,
    legislation=leg,
    vote_choice='yes'
)

# Create multiple votes
for i in range(10):
    voter = ParliamentUser.objects.create_user(
        user_id=f'voter{i}',
        name=f'Voter {i}',
        username=f'voter{i}',
        member_type='Member'
    )
    Vote.objects.create(
        user=voter,
        legislation=leg,
        vote_choice='yes' if i < 6 else 'no'  # 6 yes, 4 no
    )

# Plurality vote
Vote.objects.create(
    user=voter,
    legislation=leg,
    vote_choice='Option A'  # From plurality_options
)
```

### Creating Committees

```python
# Basic committee
committee = Committee.objects.create(
    id=1,
    code='TEST',
    name='Test Committee'
)

# Add members
committee.chairs.add(chair_user)
committee.members.add(member_user)
committee.voting_members.add(voter_user)

# Committee with role
role = Role.objects.create(
    id=1,
    code='VPT',
    name='Vice President of Testing'
)
committee = Committee.objects.create(
    code='TEST',
    name='Test Committee',
    role=role
)
```

### Creating Events

```python
# Basic event
event = Event.objects.create(
    title='Test Event',
    description='Test event description',
    date_time=timezone.now() + timedelta(days=7),
    location='Test Location',
    created_by=self.user,
    is_active=True
)

# Archived event
old_event = Event.objects.create(
    title='Old Event',
    description='Event from last year',
    date_time=timezone.now() - timedelta(days=400),
    created_by=self.user,
    archived=True,
    is_active=False
)
```

### Marking Attendance

```python
# Mark as present
Attendance.objects.create(
    user=member,
    present=True,
    created_at=timezone.now()
)

# Mark as absent
Attendance.objects.create(
    user=member,
    present=False
)

# Recent attendance (within 3 hours)
Attendance.objects.create(
    user=member,
    present=True,
    created_at=timezone.now() - timedelta(hours=2)
)
```

## Testing Views

### GET Request

```python
def test_view_loads(self):
    self.client.force_login(self.user)
    response = self.client.get(reverse('view_name'))

    self.assertEqual(response.status_code, 200)
    self.assertTemplateUsed(response, 'template.html')
```

### POST Request

```python
def test_form_submission(self):
    self.client.force_login(self.user)
    response = self.client.post(reverse('view_name'), {
        'field1': 'value1',
        'field2': 'value2'
    })

    self.assertEqual(response.status_code, 302)  # Redirect
    self.assertRedirects(response, reverse('success_page'))
```

### Check Context Data

```python
def test_context_data(self):
    response = self.client.get(reverse('view_name'))

    self.assertIn('key', response.context)
    self.assertEqual(response.context['key'], expected_value)
    self.assertEqual(len(response.context['items']), 5)
```

### Test Permissions

```python
def test_member_denied_access(self):
    self.client.force_login(self.member)
    response = self.client.get(reverse('officer_home'))

    # Should redirect or return 403
    self.assertIn(response.status_code, [302, 403])

def test_admin_has_access(self):
    self.client.force_login(self.admin)
    response = self.client.get(reverse('officer_home'))

    self.assertEqual(response.status_code, 200)
```

## Testing Model Methods

### Test Properties

```python
def test_required_yes_votes(self):
    leg = Legislation.objects.create(
        posted_by=self.user,
        available_at=timezone.now(),
        vote_mode='piecewise',
        required_number=15,
        document='test.pdf'
    )

    self.assertEqual(leg.required_yes_votes, 15)
```

### Test Methods

```python
def test_set_passed(self):
    leg = Legislation.objects.create(...)

    # Create votes
    for i in range(10):
        Vote.objects.create(user=voters[i], legislation=leg, vote_choice='yes')

    # Call method
    leg.set_passed()

    # Assert result
    self.assertTrue(leg.passed)
```

### Test String Representation

```python
def test_user_str(self):
    user = ParliamentUser.objects.create_user(...)
    expected = f'{user.name} ({user.member_type})'
    self.assertEqual(str(user), expected)
```

## Testing Vote Calculations

### Percentage Mode Pass

```python
def test_percentage_passes(self):
    leg = Legislation.objects.create(
        posted_by=self.user,
        available_at=timezone.now(),
        vote_mode='percentage',
        required_percentage='51',
        document='test.pdf'
    )

    # 60% yes (6/10)
    for i in range(6):
        Vote.objects.create(user=voters[i], legislation=leg, vote_choice='yes')
    for i in range(6, 10):
        Vote.objects.create(user=voters[i], legislation=leg, vote_choice='no')

    leg.set_passed()
    self.assertTrue(leg.passed)
```

### Piecewise Mode Pass

```python
def test_piecewise_passes(self):
    leg = Legislation.objects.create(
        posted_by=self.user,
        available_at=timezone.now(),
        vote_mode='piecewise',
        required_number=5,
        document='test.pdf'
    )

    # Exactly 5 yes votes
    for i in range(5):
        Vote.objects.create(user=voters[i], legislation=leg, vote_choice='yes')

    leg.set_passed()
    self.assertTrue(leg.passed)
```

### Plurality Mode Pass

```python
def test_plurality_passes(self):
    leg = Legislation.objects.create(
        posted_by=self.user,
        available_at=timezone.now(),
        vote_mode='plurality',
        plurality_options=['A', 'B', 'C']
    )

    # A wins with 5, B has 3, C has 2
    for i in range(5):
        Vote.objects.create(user=voters[i], legislation=leg, vote_choice='A')
    for i in range(5, 8):
        Vote.objects.create(user=voters[i], legislation=leg, vote_choice='B')
    for i in range(8, 10):
        Vote.objects.create(user=voters[i], legislation=leg, vote_choice='C')

    leg.set_passed()
    self.assertTrue(leg.passed)
```

## Common Assertions

```python
# Equality
self.assertEqual(actual, expected)
self.assertNotEqual(actual, expected)

# Boolean
self.assertTrue(condition)
self.assertFalse(condition)

# Existence
self.assertIsNone(value)
self.assertIsNotNone(value)

# Membership
self.assertIn(item, container)
self.assertNotIn(item, container)

# Greater/Less
self.assertGreater(a, b)
self.assertLess(a, b)
self.assertGreaterEqual(a, b)
self.assertLessEqual(a, b)

# Exceptions
with self.assertRaises(ExceptionType):
    code_that_raises()

# HTTP responses
self.assertEqual(response.status_code, 200)
self.assertTemplateUsed(response, 'template.html')
self.assertContains(response, 'text')
self.assertRedirects(response, '/url/')

# QuerySets
self.assertQuerysetEqual(qs1, qs2)
self.assertEqual(qs.count(), 5)
```

## Test Fixtures

### Creating Multiple Voters

```python
def create_voters(self, count=10):
    """Helper to create multiple voters"""
    voters = []
    for i in range(count):
        voter = ParliamentUser.objects.create_user(
            user_id=f'voter{i}',
            name=f'Voter {i}',
            username=f'voter{i}',
            member_type='Member'
        )
        Attendance.objects.create(user=voter, present=True)
        voters.append(voter)
    return voters
```

### Creating Vote Distribution

```python
def create_vote_distribution(self, leg, yes_count, no_count, abstain_count=0):
    """Helper to create specific vote distribution"""
    voters = self.create_voters(yes_count + no_count + abstain_count)

    for i in range(yes_count):
        Vote.objects.create(user=voters[i], legislation=leg, vote_choice='yes')

    for i in range(yes_count, yes_count + no_count):
        Vote.objects.create(user=voters[i], legislation=leg, vote_choice='no')

    if abstain_count > 0:
        for i in range(yes_count + no_count, yes_count + no_count + abstain_count):
            Vote.objects.create(user=voters[i], legislation=leg, vote_choice='abstain')
```

## Running Specific Tests

```bash
# Run all tests in a class
python manage.py test src.test_comprehensive.VoteModeTestCase

# Run single test method
python manage.py test src.test_comprehensive.VoteModeTestCase.test_percentage_mode_passes

# Run with pattern
python manage.py test --pattern="test_vote*"

# Run with debug output
python manage.py test --verbosity=2

# Run and stop on first failure
python manage.py test --failfast
```

## Debugging Tests

### Print Debug Info

```python
def test_something(self):
    print(f"Votes: {Vote.objects.count()}")
    print(f"User: {self.user.username}")
    # Test continues...
```

### Use Debugger

```python
def test_something(self):
    import pdb; pdb.set_trace()
    # Execution pauses here
    # Type 'c' to continue, 'n' for next line
```

### Check Database State

```python
def test_something(self):
    # Before action
    count_before = Legislation.objects.count()

    # Perform action
    # ...

    # After action
    count_after = Legislation.objects.count()

    self.assertEqual(count_after, count_before + 1)
```

## Edge Cases to Test

1. **Empty data**: No votes, no members, no options
2. **Boundary values**: Exactly 51%, 0 votes, 100% votes
3. **Invalid data**: Negative numbers, future dates, null values
4. **Permissions**: Wrong user type, not logged in
5. **Concurrent access**: Multiple users voting simultaneously
6. **Data integrity**: Unique constraints, foreign keys

## Best Practices

1. ✅ Use descriptive test names
2. ✅ Test one thing per test
3. ✅ Use setUp() for common data
4. ✅ Clean up in tearDown() if needed
5. ✅ Test both success and failure paths
6. ✅ Use helper methods for repeated code
7. ✅ Document complex test scenarios
8. ✅ Keep tests independent
9. ✅ Test edge cases
10. ✅ Use meaningful assertion messages

```python
self.assertTrue(
    leg.passed,
    f"Expected legislation to pass with {yes_votes} yes votes"
)
```

"""Fixed blocker categories so reports, filters and approvals share one vocabulary.

Workers still explain themselves in free text, but the category is a closed set
the gateway validates. It is what the dashboard filters on, what approvals act
on and what the coordinator can summarise without guessing.
"""
BLOCKERS = {
    'missing_toolchain': 'Required toolchain is unavailable in the container',
    'missing_permissions': 'Assigned permissions do not allow this work',
    'owner_restriction': 'The repository owner prohibited this integration',
    'read_only_role': 'The assigned role is read-only',
    'task_conflict': 'This conflicts with the agent\'s current task',
    'state_conflict': 'Checkout state (merge, rebase or conflicts) blocks the work',
    'verification_failed': 'Git verification did not confirm the result',
    'approval_expired': 'The operator approval expired before delivery',
    'approval_stale': 'The checkout changed after the operator approval',
    'unspecified': 'Unspecified blocker',
}

# Categories a worker may report. Gateway-set categories are deliberately absent.
WORKER_BLOCKERS = ('missing_toolchain', 'missing_permissions', 'owner_restriction',
                   'read_only_role', 'task_conflict', 'state_conflict', 'unspecified')

# Categories the operator can act on by provisioning or re-verifying.
TOOLCHAIN = 'missing_toolchain'


def normalize(value):
    """Map an unknown or absent worker value onto the fixed vocabulary."""
    return value if isinstance(value, str) and value in WORKER_BLOCKERS else 'unspecified'


def label(value):
    return BLOCKERS.get(value, BLOCKERS['unspecified'])

import logging

_logger = logging.getLogger(__name__)


def migrate(env, version):
    env.cr.execute(
        """
            ALTER TABLE crossovered_budget_lines
            RENAME COLUMN account_analytic_id TO account_id
        """
    )

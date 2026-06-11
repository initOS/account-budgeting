# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from_string = fields.Datetime.from_string


# ---------------------------------------------------------
# Budgets
# ---------------------------------------------------------
class AccountBudgetPost(models.Model):
    _name = "account.budget.post"
    _order = "name"
    _description = "Budgetary Position"

    name = fields.Char(required=True)
    account_ids = fields.Many2many(
        comodel_name="account.account",
        relation="account_budget_rel",
        column1="budget_id",
        column2="account_id",
        string="Accounts",
        domain="[('active', '=', True), ('company_ids', 'in', company_id)]",
    )
    crossovered_budget_line_ids = fields.One2many(
        comodel_name="crossovered.budget.lines",
        inverse_name="general_budget_id",
        string="Budget Lines",
    )
    company_id = fields.Many2one(
        comodel_name="res.company", required=True, default=lambda self: self.env.company
    )

    def _check_account_ids(self, vals=None):
        # Raise an error to prevent the account.budget.post to have not
        # specified account_ids.
        # This check is done on create because require=True doesn't work on
        # Many2many fields.
        for rec in self:
            if vals and "account_ids" in vals:
                account_ids = rec.new({"account_ids": vals["account_ids"]}).account_ids
            else:
                account_ids = rec.account_ids
            if not account_ids:
                raise ValidationError(
                    self.env._("The budget must have at least one account.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_account_ids(vals)
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        self._check_account_ids()
        return res


class CrossoveredBudget(models.Model):
    _name = "crossovered.budget"
    _description = "Budget"
    _inherit = ["mail.thread"]

    name = fields.Char(string="Budget Name", required=True)
    creating_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Responsible",
        default=lambda self: self.env.user,
    )
    date_from = fields.Date(string="Start Date", required=True)
    date_to = fields.Date(string="End Date", required=True)
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("cancel", "Cancelled"),
            ("confirm", "Confirmed"),
            ("validate", "Validated"),
            ("done", "Done"),
        ],
        string="Status",
        default="draft",
        index=True,
        required=True,
        readonly=True,
        copy=False,
        tracking=True,
    )
    budget_type = fields.Selection(
        string="Budget Type",
        selection=[
            ("revenue", "Revenue"),
            ("expense", "Expense"),
            ("both", "Both"),
        ],
        required=True,
        default="expense",
        copy=False,
    )
    crossovered_budget_line_ids = fields.One2many(
        comodel_name="crossovered.budget.lines",
        inverse_name="crossovered_budget_id",
        string="Budget Lines",
        copy=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company", required=True, default=lambda self: self.env.company
    )

    def action_budget_confirm(self):
        self.write({"state": "confirm"})

    def action_budget_draft(self):
        self.write({"state": "draft"})

    def action_budget_validate(self):
        self.write({"state": "validate"})

    def action_budget_cancel(self):
        self.write({"state": "cancel"})

    def action_budget_done(self):
        self.write({"state": "done"})

    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        return self.env["analytic.plan.fields.mixin"]._patch_view(arch, view, view_type)


class CrossoveredBudgetLines(models.Model):
    _name = "crossovered.budget.lines"
    _inherit = ["analytic.plan.fields.mixin"]
    _description = "Budget Line"

    crossovered_budget_id = fields.Many2one(
        comodel_name="crossovered.budget",
        string="Budget",
        ondelete="cascade",
        index=True,
        required=True,
    )
    general_budget_id = fields.Many2one(
        comodel_name="account.budget.post", string="Budgetary Position", required=True
    )
    date_from = fields.Date(string="Start Date", required=True)
    date_to = fields.Date(string="End Date", required=True)
    paid_date = fields.Date()
    planned_amount = fields.Float(required=True, digits=0)
    practical_amount = fields.Float(compute="_compute_practical_amount", digits=0)
    theoretical_amount = fields.Float(compute="_compute_theoretical_amount", digits=0)
    percentage = fields.Float(compute="_compute_percentage", string="Achievement")
    company_id = fields.Many2one(
        related="crossovered_budget_id.company_id", store=True, readonly=True
    )

    @api.depends("general_budget_id.account_ids", "date_from", "date_to", "account_id")
    def _compute_practical_amount(self):
        project_plan, other_plans = self.env["account.analytic.plan"]._get_all_plans()
        plan_fnames = [
            fname
            for plan in project_plan | other_plans
            if (fname := plan._column_name()) in self
        ]

        for line in self:
            result = 0.0
            acc_ids = line.general_budget_id.account_ids.ids
            date_to = line.date_to
            date_from = line.date_from
            if date_from and date_to and acc_ids:
                sign = -1 if line.crossovered_budget_id.budget_type == "expense" else 1

                domain = (
                    [("account_id", "=", line.account_id.id)] if line.account_id else []
                )

                for fname in plan_fnames:
                    if acc := line[fname]:
                        domain.append((fname, "=", acc.id))

                if line.account_id:
                    data = self.env["account.analytic.line"]._read_group(
                        [
                            *domain,
                            ("date", ">=", date_from),
                            ("date", "<=", date_to),
                            ("general_account_id", "in", acc_ids),
                        ],
                        aggregates=["amount:sum"],
                    )
                    result = sign * data[0][0] if data else 0.0
                else:
                    data = self.env["account.move.line"]._read_group(
                        [
                            ("account_id", "in", acc_ids),
                            ("date", ">=", date_from),
                            ("date", "<=", date_to),
                            ("parent_state", "=", "posted"),
                        ],
                        aggregates=["balance:sum"],
                    )
                    result = -sign * data[0][0] if data else 0.0
            line.practical_amount = result

    @api.depends("paid_date", "date_from", "date_to", "planned_amount")
    def _compute_theoretical_amount(self):
        today = fields.Datetime.now()
        for line in self:
            # Used for the report

            if line.paid_date:
                if from_string(line.date_to) <= from_string(line.paid_date):
                    theo_amt = 0.00
                else:
                    theo_amt = line.planned_amount
            elif line.date_from and line.date_to:
                line_timedelta = from_string(line.date_to) - from_string(line.date_from)
                elapsed_timedelta = from_string(today) - from_string(line.date_from)

                if elapsed_timedelta.days < 0:
                    # If the budget line has not started yet, theoretical
                    # amount should be zero
                    theo_amt = 0.00
                elif line_timedelta.days > 0 and from_string(today) < from_string(
                    line.date_to
                ):
                    # If today is between the budget line date_from and
                    # date_to
                    theo_amt = (
                        elapsed_timedelta.total_seconds()
                        / line_timedelta.total_seconds()
                    ) * line.planned_amount
                else:
                    theo_amt = line.planned_amount
            else:
                theo_amt = 0.00
            line.theoretical_amount = theo_amt

    @api.depends("theoretical_amount", "practical_amount")
    def _compute_percentage(self):
        for line in self:
            if line.theoretical_amount != 0.00:
                line.percentage = (
                    float((line.practical_amount or 0.0) / line.theoretical_amount)
                    * 100
                )
            else:
                line.percentage = 0.00

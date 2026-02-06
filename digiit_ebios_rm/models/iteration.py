from odoo import models, fields,_
from odoo.tools.translate import _

class Iteration(models.Model):
    _name = 'digiit.ebios_rm.iteration'
    _description=_('Iteration')

    operational_scenario = fields.Text(string=_("Déscription du scénario opérationnel"),required=True)
    likelihood_calculation_method = fields.Selection(
        [
            ('express', _("Express")),
            ('standard', _("Standard")),
            ('advanced', _("Avancée")),
        ],
        string=_('Méthode de calcul de vraisemblance'),
        default="express",
        required=True
    )

    pip_maturity_evaluation = fields.Char(string=_("Evaluation de la maturité PiP"),required=True)
    maturity_socle_evalutation = fields.Char(string=_("Évaluation du socle de maturité"),required=True)
    limit_date = fields.Date(string=_("Date limite"))
    study_id = fields.Many2one(
        'digiit.ebios_rm.study',
        string=_("Etude"),
        ondelete='cascade'
    )







    


    






        
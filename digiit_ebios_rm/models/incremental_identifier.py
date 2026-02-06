from odoo import models, fields,_,api

class IncrementalIdentifier(models.AbstractModel):
    _name = 'digiit.ebios_rm.incremental.identifier'


    identifier = fields.Char(string=_('ID'), compute="_compute_identifier", readonly=True)
    prefixed_identifier = fields.Char(string=_('ID'), compute="_compute_prefixed_identifier", readonly=True)
    prefix = 'ID-'
    related_field = ''

    def _compute_identifier(self):
        try:
            for record in self:
                recs = list(self.env[record._name].search([(record.related_field,'=',record[record.related_field].id)]))
                if(record in recs):
                    index = recs.index(record)
                    record.identifier = index+ 1
                else:
                    record.identifier = len(recs)+ 1
        finally:
            print("")
            

    @api.depends("identifier")
    def _compute_prefixed_identifier(self):
        for record in self:
            if hasattr(record,'identifier'):
                record.prefixed_identifier = f"{record.prefix}{record.identifier or ''}"
            else:
                record.prefixed_identifier = ''
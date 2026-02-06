from odoo import models, fields


class ApplicationStatusScale(models.Model):
    _name = 'digiit.ebios_rm.application.status.scale'


    level = fields.Integer(string= "Niveau", required= True, tracking= True)
    name = fields.Char(string= "Nom", required= True, tracking= True)
    color = fields.Char(string= "Couleur", required= True, tracking= True)
    description = fields.Text(string= "Description", tracking= True)
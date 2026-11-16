"""Remove the saved HERE Places API credential after removing the integration."""


def migrate(cr, version):
    cr.execute(
        "DELETE FROM ir_config_parameter WHERE key = %s",
        ('crm_lead_discovery.here_api_key',),
    )

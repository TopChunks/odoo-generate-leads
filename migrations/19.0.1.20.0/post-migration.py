"""Remove the saved Apollo credential when upgrading this addon."""


def migrate(cr, version):
    cr.execute(
        "DELETE FROM ir_config_parameter WHERE key = %s",
        ('crm_lead_discovery.apollo_api_key',),
    )

from starlette.templating import Jinja2Templates

import jinja2
from filmdrop_titiler.application.settings import ApiSettings

api_settings = ApiSettings()

# custom template directory
templates_location: list[jinja2.BaseLoader] = (
    [jinja2.FileSystemLoader(api_settings.template_directory)]
    if api_settings.template_directory
    else []
)
# default template directory
templates_location.extend(
    [
        jinja2.PackageLoader("filmdrop_titiler.application", "templates"),
        jinja2.PackageLoader("titiler.core", "templates"),
    ]
)

jinja2_env = jinja2.Environment(
    autoescape=jinja2.select_autoescape(["html"]),
    loader=jinja2.ChoiceLoader(templates_location),
)
titiler_templates = Jinja2Templates(env=jinja2_env)

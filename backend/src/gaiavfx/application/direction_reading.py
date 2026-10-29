"""Direction as the application reads it (ADR 0010 §7, ADR 0019 §3).

Direction judges a binding by the inputs it was approved with. Two kinds of binding have inputs that live in other
modules: an assembled sheet's tiles (Generation) and a set view's camera and hidden regions (Sets). This service
carries both readers, and every plan, preparation and binding check reads Direction through it; a set view read
without it is refused, never judged by a survey's inputs.
"""

from gaiavfx.application.set_views import set_view_inputs
from gaiavfx.application.sheet_recipes import sheet_reference
from gaiavfx.modules.direction.service import DirectionService

DIRECTION = DirectionService(sheet_reader=sheet_reference, view_reader=set_view_inputs)

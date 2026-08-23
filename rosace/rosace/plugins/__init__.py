"""
Stage plugin registry.
Importing this module is enough to instantiate every plugin.
"""

from ..models import OrchestratorStage, SessionStagePlugin, ObservationStagePlugin
from .init_session    import InitSessionPlugin
from .pipeline_loader import validate_subplugin_catalogue
from .schedulers      import load_scheduler
from .sequencer       import SequencerPlugin
from .reduction       import ReductionPlugin
from .sharing         import SharingPlugin
from .archive         import ArchivePlugin
from .close_session   import CloseSessionPlugin

validate_subplugin_catalogue()

plugins: dict[OrchestratorStage, SessionStagePlugin | ObservationStagePlugin] = {
    OrchestratorStage.INIT_SESSION:  InitSessionPlugin(),
    OrchestratorStage.SCHEDULER:     load_scheduler(),
    OrchestratorStage.SEQUENCER:     SequencerPlugin(),
    OrchestratorStage.REDUCTION:     ReductionPlugin(),
    OrchestratorStage.SHARING:       SharingPlugin(),
    OrchestratorStage.ARCHIVE:       ArchivePlugin(),
    OrchestratorStage.CLOSE_SESSION: CloseSessionPlugin(),
}

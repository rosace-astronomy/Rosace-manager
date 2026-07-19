"""
Registre des plugins de stage.
Importer ce module suffit à instancier tous les plugins.
"""

from ..models import OrchestratorStage, SessionStagePlugin, ObservationStagePlugin
from .init_session  import InitSessionPlugin
from .scheduler     import SchedulerPlugin
from .sequencer     import SequencerPlugin
from .reduction     import ReductionPlugin
from .sharing       import SharingPlugin
from .archive       import ArchivePlugin
from .close_session import CloseSessionPlugin

plugins: dict[OrchestratorStage, SessionStagePlugin | ObservationStagePlugin] = {
    OrchestratorStage.INIT_SESSION:  InitSessionPlugin(),
    OrchestratorStage.SCHEDULER:     SchedulerPlugin(),
    OrchestratorStage.SEQUENCER:     SequencerPlugin(),
    OrchestratorStage.REDUCTION:     ReductionPlugin(),
    OrchestratorStage.SHARING:       SharingPlugin(),
    OrchestratorStage.ARCHIVE:       ArchivePlugin(),
    OrchestratorStage.CLOSE_SESSION: CloseSessionPlugin(),
}

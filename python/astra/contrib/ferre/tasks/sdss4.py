
import os
from astra.tasks.io.sdss4 import SDSS4ApStarFile
from astra.tasks.targets import LocalTarget
from astra.contrib.ferre.tasks.ferre import EstimateStellarParametersGivenApStarFileBase
from astra.contrib.ferre.tasks.aspcap import (
    EstimateStellarParametersGivenMedianFilteredApStarFileBase,
    InitialEstimateOfStellarParametersGivenApStarFileBase,
    IterativeEstimateOfStellarParametersGivenApStarFileBase
)
from astra.contrib.ferre.tasks.targets import FerreResult

class SDSS4Mixin:

    def output(self):
        """ The outputs generated by the task. """

        if self.is_batch_mode:
            return [task.output() for task in self.get_batch_tasks()]

        path = os.path.join(
            self.output_base_dir,
            f"star/{self.telescope}/{self.field}/",
            f"apStar-{self.apred}-{self.telescope}-{self.obj}-{self.task_id}.pkl"
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)

        return {
            "database": FerreResult(self),
            "spectrum": LocalTarget(path)
        }


class EstimateStellarParametersGivenSDSS4ApStarFile(EstimateStellarParametersGivenApStarFileBase, SDSS4ApStarFile, SDSS4Mixin):
    
    """ Use FERRE to estimate stellar parameters given a SDSS-IV ApStar file. """
    
    observation_task = SDSS4ApStarFile



class EstimateStellarParametersGivenMedianFilteredSDSS4ApStarFile(EstimateStellarParametersGivenMedianFilteredApStarFileBase, SDSS4ApStarFile, SDSS4Mixin):

    def requires(self):
        """ The requirements of this task, which include the previous estimate. """
        requirements = super(EstimateStellarParametersGivenMedianFilteredApStarFileBase, self).requires()
        requirements.update(
            previous_estimate=EstimateStellarParametersGivenSDSS4ApStarFile(**self.get_common_param_kwargs(EstimateStellarParametersGivenSDSS4ApStarFile))
        )
        return requirements



class InitialEstimateOfStellarParametersGivenSDSS4ApStarFile(InitialEstimateOfStellarParametersGivenApStarFileBase):

    """
    A task that dispatches an ApStarFile to multiple FERRE grids, in a similar way done by ASPCAP in SDSS-IV.
    """

    task_factory = EstimateStellarParametersGivenSDSS4ApStarFile




class IterativeEstimateOfStellarParametersGivenSDSS4ApStarFile(IterativeEstimateOfStellarParametersGivenApStarFileBase, SDSS4ApStarFile, SDSS4Mixin):

    def requires(self):
        """ This task requires the initial estimates of stellar parameters from many grids. """
        return InitialEstimateOfStellarParametersGivenSDSS4ApStarFile(
            **self.get_common_param_kwargs(InitialEstimateOfStellarParametersGivenSDSS4ApStarFile)
        )

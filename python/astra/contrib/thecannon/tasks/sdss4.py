import os
import astropy.table
from astra.tasks.io.sdss4 import SDSS4ApStarFile as ApStarFile
from astra.tasks.continuum import Sinusoidal
from astra.tasks.targets import (DatabaseTarget, LocalTarget)

from astra.contrib.thecannon.tasks.train import TrainTheCannonGivenTrainingSetTask
from astra.contrib.thecannon.tasks.test import EstimateStellarParametersGivenApStarFileBase

from sqlalchemy import Column, Float

class TheCannonResult(DatabaseTarget):

    """ A row in a database representing a result from The Cannon. """

    # TODO: This should be updated when the "production" model of The Cannon is decided.
    teff = Column("teff", Float)
    logg = Column("logg", Float)
    fe_h = Column("fe_h", Float)
    u_teff = Column("u_teff", Float)
    u_logg = Column("u_logg", Float)
    u_fe_h = Column("u_fe_h", Float)
    

class ContinuumNormalizeIndividualVisitsInSDSS4ApStarFile(Sinusoidal, ApStarFile):

    """
    A pseudo-continuum normalisation task for individual visit spectra 
    in ApStarFiles using a sum of sines and cosines to model the continuum.
    """

    # Row 0 is individual pixel weighting
    # Row 1 is global pixel weighting
    # Row 2+ are the individual visits.
    # We will just analyse them all because it's cheap.

    def requires(self):
        return ApStarFile(**self.get_common_param_kwargs(ApStarFile))





class EstimateStellarParametersGivenSDSS4ApStarFile(EstimateStellarParametersGivenApStarFileBase, ContinuumNormalizeIndividualVisitsInSDSS4ApStarFile):


    def requires(self):
        requirements = super(EstimateStellarParametersGivenSDSS4ApStarFile, self).requires()
        requirements.update(
            observation=ContinuumNormalizeIndividualVisitsInSDSS4ApStarFile(**self.get_common_param_kwargs(ContinuumNormalizeIndividualVisitsInSDSS4ApStarFile))
        )
        return requirements
        

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
            "etc": LocalTarget(path),
            "database": TheCannonResult(self)
        }
        


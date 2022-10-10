from re import A
import numpy as np
from scipy.ndimage.filters import median_filter

import pickle
from astra import log
from astra.utils import expand_path
from astra.contrib.ferre import bitmask
from astra.contrib.ferre.utils import read_ferre_headers
from astra.database.astradb import Task
from astra.tools.continuum.base import Continuum
from astra.tools.spectrum import Spectrum1D
from astropy.io import fits

from typing import Optional, List, Tuple, Union
from astra.tools.spectrum import SpectralAxis

class MedianFilter(Continuum):

    """Use a median filter to represent the stellar continuum."""

    def __init__(
        self,
        upstream_task_id: int,
        median_filter_width: Optional[int] = 151,
        bad_minimum_flux: Optional[float] = 0.01,
        non_finite_err_value: Optional[float] = 1e10,
        valid_continuum_correction_range: Optional[Tuple[float]] = (0.1, 10.0),
        mode: Optional[str] = "constant",
        spectral_axis: Optional[SpectralAxis] = None,
        regions: Optional[List[Tuple[float, float]]] = None,
        mask: Optional[Union[str, np.array]] = None,
        fill_value: Optional[Union[int, float]] = np.nan,
        **kwargs
    ) -> None:
        (
            """
        :param median_filter_width: [optional]
            The width (int) for the median filter (default: 151).

        :param bad_minimum_flux: [optional]
            The value at which to set pixels as bad and median filter over them. This should be a float,
            or `None` to set no low-flux filtering (default: 0.01).

        :param non_finite_err_value: [optional]
            The error value to set for pixels with non-finite fluxes (default: 1e10).

        :param valid_continuum_correction_range: [optional]
            A (min, max) tuple of the bounds that the final correction can have. Values outside this range will be set
            as 1.
        """
            + Continuum.__init__.__doc__
        )
        super(MedianFilter, self).__init__(
            spectral_axis=spectral_axis,
            regions=regions,
            mask=mask,
            fill_value=fill_value,
            **kwargs,
        )
        self.upstream_task_id = upstream_task_id
        self.median_filter_width = median_filter_width
        self.bad_minimum_flux = bad_minimum_flux
        self.non_finite_err_value = non_finite_err_value
        self.valid_continuum_correction_range = valid_continuum_correction_range
        self.mode = mode
        return None


    def _initialize(self, spectrum, task=None):
        try:
            self._initialized_args
        except AttributeError:
            if self.regions is None:
                # Get the regions from the model wavelength segments.        
                if task is None:
                    task = Task.get(self.upstream_task_id)

                regions = []
                for header in read_ferre_headers(expand_path(task.parameters["header_path"]))[1:]:
                    crval, cdelt = header["WAVE"]
                    npix = header["NPIX"]
                    regions.append((10**crval, 10**(crval + cdelt * npix)))
                self.regions = regions
        
            self._initialized_args = super(MedianFilter, self)._initialize(spectrum)
        finally:
            return self._initialized_args
        
        
    def fit(self, spectrum: Spectrum1D, hdu=3):

        task = Task.get(self.upstream_task_id)
        region_slices, region_masks = self._initialize(spectrum, task)

        #flux/continuum and model_flux
        # before:
        # (ferre_flux / continuum)  and (model_flux)
        # now:
        # ferre_flux and (model_flux / continuum)
        # ratio = continuum * (ferre_flux / model_flux)

        # This is an astraStar-FERRE product, but let's just use fits.open
        with fits.open(task.output_data_products[0].path) as image:
            # How do we decide on the HDU for this product just from the spectrum?
            continuum = image[hdu].data["CONTINUUM"]
            flux = image[hdu].data["FERRE_FLUX"]
            rectified_model_flux = image[hdu].data["MODEL_FLUX"] / continuum
                    
        N, P = flux.shape
        self._continuum = np.nan * np.ones((N, P))
        for i in range(N):
            for region_mask in region_masks:
                flux_region, model_flux_region = (flux[i, region_mask].copy(), rectified_model_flux[i, region_mask].copy())

                # TODO: It's a little counter-intuitive how this is documented, so we should fix that.
                #       Or allow for a MAGIC number 5 instead.
                median = median_filter(flux[i, region_mask], [self.median_filter_width], mode=self.mode)

                bad = np.where(
                    (flux_region < self.bad_minimum_flux) | (flux_region > (np.nanmedian(flux_region) + 3 * np.nanstd(flux_region)))
                )[0]
                flux_region[bad] = median[bad]

                ratio_region = flux_region / model_flux_region
                self._continuum[i, region_mask] = median_filter(
                    ratio_region, 
                    [self.median_filter_width], 
                    mode=self.mode,
                    cval=1.0
                )

        scalars = np.nanmedian(spectrum.flux.value / self._continuum, axis=1)
        self._continuum *= scalars
        return None


    def __call__(
        self,
        spectrum: Spectrum1D,
        theta: Optional[Union[List, np.array, Tuple]] = None,
        **kwargs
    ) -> np.ndarray:
        if theta is not None:
            log.warning(f"Continuum coefficients ignored here")
        return self._continuum



def median_filtered_correction(
    wavelength,
    normalised_observed_flux,
    normalised_observed_flux_err,
    normalised_model_flux,
    segment_indices=None,
    median_filter_width=151,
    bad_minimum_flux=0.01,
    non_finite_err_value=1e10,
    valid_continuum_correction_range=(0.1, 10.0),
    mode="nearest",
    **kwargs,
):
    """
    Perform a median filter correction to the normalised observed spectrum, given some best-fitting normalised model flux.

    :param wavelength:
        A 1-D array of wavelength values.

    :param normalised_observed_flux:
        The pseudo-continuum normalised observed flux array. This should be the same format as `wavelength`.

    :param normalised_observed_flux_err:
        The 1-sigma uncertainty in the pseudo-continuum normalised observed flux array. This should have the same format as `wavelength`.

    :param normalised_model_flux:
        The best-fitting pseudo-continuum normalised model flux. This should have the same format as `wavelength`.

    :param median_filter_width: [optional]
        The width (int) for the median filter (default: 151).

    :param bad_minimum_flux: [optional]
        The value at which to set pixels as bad and median filter over them. This should be a float,
        or `None` to set no low-flux filtering (default: 0.01).

    :param non_finite_err_value: [optional]
        The error value to set for pixels with non-finite fluxes (default: 1e10).

    :param valid_continuum_correction_range: [optional]
        A (min, max) tuple of the bounds that the final correction can have. Values outside this range will be set
        as 1.

    :param mode: [optional]
        The mode to supply to `scipy.ndimage.filters.median_filter` (default: nearest).

    :returns:
        A two-length tuple of the pseudo-continuum segments, and the corrected pseudo-continuum-normalised observed flux errors.
    """

    """
    if isinstance(wavelength, np.ndarray):
        wavelength = (wavelength, )
    if isinstance(normalised_observed_flux, np.ndarray):
        normalised_observed_flux = (normalised_observed_flux, )
    if isinstance(normalised_observed_flux_err, np.ndarray):
        normalised_observed_flux_err = (normalised_observed_flux_err, )
    if isinstance(normalised_model_flux, np.ndarray):
        normalised_model_flux = (normalised_model_flux, )

    N = len(normalised_observed_flux)
    if isinstance(width, int):
        width = tuple([width] * N)
    if isinstance(bad_minimum_flux, float):
        bad_minimum_flux = tuple([bad_minimum_flux] * N)
    """
    wavelength = np.atleast_1d(wavelength).flatten()
    normalised_observed_flux = np.atleast_1d(normalised_observed_flux).flatten()
    normalised_observed_flux_err = np.atleast_1d(normalised_observed_flux_err).flatten()
    normalised_model_flux = np.atleast_1d(normalised_model_flux).flatten()

    data = (
        wavelength,
        normalised_observed_flux,
        normalised_observed_flux_err,
        normalised_model_flux,
    )

    if segment_indices is None:
        segment_indices = np.array([[0, wavelength.size]])

    continuum = np.nan * np.ones_like(normalised_observed_flux)

    for j, (start, end) in enumerate(segment_indices):

        wl = wavelength[start:end]
        flux = normalised_observed_flux[start:end]
        flux_err = normalised_observed_flux_err[start:end]
        model_flux = normalised_model_flux[start:end]

        # TODO: It's a little counter-intuitive how this is documented, so we should fix that.
        #       Or allow for a MAGIC number 5 instead.
        median = median_filter(flux, [5 * median_filter_width], mode=mode)

        bad = np.where(flux < bad_minimum_flux)[0]

        flux_copy = flux.copy()
        flux_copy[bad] = median[bad]

        ratio = flux_copy / model_flux

        # Clip edges.
        # ratio[0] = np.median(ratio[:E])
        # ratio[-1] = np.median(ratio[-E:])

        continuum[start:end] = median_filter(ratio, [median_filter_width], mode=mode)

        """
        err_copy = err.copy() * flux / flux_copy

        non_finite = ~np.isfinite(err_copy)
        err_copy[non_finite] = non_finite_err_value

        # Get ratio of observed / model and check that edge is reasonable.
        ratio = flux_copy / model
        correction = scipy.ndimage.filters.median_filter(
            ratio,
            width[i],
            mode=mode,
            **kwargs
        )
        bad = (~np.isfinite(correction)) + np.isclose(correction, 0)
        correction[bad] = 1.0

        segment_continuum.append(correction)
        segment_errs.append(err_copy)
        """

    bad = ~np.isfinite(continuum)
    if valid_continuum_correction_range is not None:
        l, u = valid_continuum_correction_range
        bad += (continuum < l) + (continuum > u)

    continuum[bad] = 1

    return continuum


MedianFilterNormalizationWithErrorInflation = MedianFilter

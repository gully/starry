"""Analysis of HD189733b 8 micron Spitzer secondary eclipses."""

import sys, os
import matplotlib.pyplot as plt
from matplotlib import gridspec
from matplotlib import rcParams
import numpy as np
import pandas as pd
import wget
from tqdm import tqdm
from scipy.optimize import minimize
from scipy.io.idl import readsav
import emcee, corner

rcParams["text.usetex"] = False

import starry

class EclipseData(object):
    """
    Load the HD189733b secondary eclipse data from Knutson et al. (2007)

    Parameters
    ----------
    plot : bool
        Plot the data

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        Light curve data with (time, flux, errors)
    df_med : :class:`pandas.DataFrame`
        Light curve rolling median
    df_std : :class:`pandas.DataFrame`
        Light curve rolling std

    Note
    ----
    If ``plot = True`` then :class:`matplotlib` ``fig`` and ``ax`` objects will
    also be saved as attributes.

    Example
    -------
    >>> data = EclipseData(plot = True)
    """

    def __init__(self, plot = False):

        # Check if datafile exists
        filename = "light_curve.sav"
        if not os.path.exists(filename):
            # Download file
            url = "https://www.dropbox.com/s/nhw8t3sqbfro585/light_curve.sav?dl=1"
            filename = wget.download(url)

        # Load data from IDL save (should probably change this...)
        idl = readsav(filename)

        # Make pandas DataFrame
        df = pd.DataFrame(np.array(idl['light_curve']).byteswap().newbyteorder(), columns=["time", "flux"])

        # Calculate rolling median and standard deviation
        df_med = df.rolling(5000, center=False).median()
        df_std = df.rolling(5000, center=False).std()

        # Calculate data uncertainties as the median rolling standard deviation
        assumed_errors = np.nanmedian(df_std.flux)
        df['std'] = pd.Series(assumed_errors * np.ones(len(df.flux)), index = df.index)

        time = df['time'].values
        y = df['flux'].values
        yerr = df['std'].values

        # Mask phasecurve
        tstart = 0.045
        mask = (time > -tstart) & (time < tstart)

        if plot:

            # Make plot
            fig, ax = plt.subplots(1, figsize=(16, 8))
            ax.plot(time, y, "o", alpha = 1., ms = 0.1, color='C0', label = "data")
            ax.plot(df_med['time'], df_med['flux'], label = "rolling median")
            ax.set_xlabel('Time [days]', fontsize=14, fontweight='bold');
            ax.set_ylabel('Normalized Flux', fontsize=14, fontweight='bold');
            ax.set_ylim(0.995, 1.002)
            ax.set_xlim(-tstart, tstart)
            ax.legend();

            # Save fig, ax as attributes
            self.fig = fig
            self.ax = ax

        # Save things as attributes
        self.df = df
        self.df_med = df_med
        self.df_std = df_std
        self.time = df['time'].values[mask]
        self.y = df['flux'].values[mask]
        self.yerr = df['std'].values[mask]

def hotspot_offset(p):
    """Calculate the latitude and longitude of the hot spot in degrees"""
    x = p[2] / np.sqrt(np.sum(p[:3] ** 2))
    y = p[0] / np.sqrt(np.sum(p[:3] ** 2))
    z = p[1] / np.sqrt(np.sum(p[:3] ** 2))
    lat = np.arcsin(y) * 180 / np.pi
    lon = np.arccos(z / np.sqrt(1 - y ** 2)) * 180 / np.pi
    return lat, lon

def set_coeffs(p, planet):
    """Set the coefficients of the planet map."""
    y1m1, y10, y11, L = p
    #y1m1, y10, y11 = p
    planet.map[1, -1] = y1m1
    planet.map[1, 0] = y10
    planet.map[1, 1] = y11
    planet.L = L

def gen_coeffs():
    """Generate random initial conditions."""
    y1m1 = np.random.randn()
    y10 = np.random.randn()
    y11 = np.random.randn()
    L = np.random.randn()
    return np.array([y1m1, y10, y11, L])
    #return np.array([y1m1, y10, y11])

def gen_coeffs_in_bounds(planet):
    """Generate random initial conditions with finite lnprior."""
    while True:
        t0 = np.array(gen_coeffs())
        if np.isfinite(lnprior(t0, planet)):
            break
    return t0

def lnprior_intensity(p):
    """
    Ensure that the minimum intensity from the planet is positive

    This has been replaced with: planet.map.psd()
    """
    cmag = np.sqrt(p[0]**2 + p[1]**2 + p[2]**2)
    if cmag >= np.sqrt(1./3.):
        return -np.inf
    else:
        return 0

def lnprior(p, planet):
    """Log prior"""

    lp = 0.0

    # Loosely informative log prior probability
    if np.any(p < -5) or np.any(p > 5):
        lp += -np.inf
    else:
        lp += 0.0

    # Constrain the Luminosity more than harmonic coeffs
    #"""
    if (p[-1] < 0) or (p[-1] > 0.01):
        lp += -np.inf
    else:
        lp += 0.0
    #"""

    # Ensure that the minimum intensity from the planet is positive
    #     replaced: lp += lnprior_intensity(p)
    #"""
    set_coeffs(p, planet)
    if planet.map.psd():
        lp += 0.0
    else:
        lp += -np.inf
    #"""

    return lp


def lnlike(p, time, y, yerr, system, planet):
    """Log likelihood."""
    ll = lnprior(p, planet)
    if np.isinf(ll):
        return ll

    # Set the coeffs and compute the flux
    set_coeffs(p, planet)
    system.compute(time)

    # Normalize the model so that the *total* flux baseline is unity
    #model = system.flux / system.flux[0]
    model = system.flux / system.flux[0]

    # Compute the chi-squared
    chisq = np.sum((y - model) ** 2 / yerr ** 2)# / len(y)

    ll += -0.5 * chisq

    return ll

def neglnlike(*args):
    """Negative log likelihood"""
    ll = lnlike(*args)
    return -ll

def lnlike_grad(p, time, y, yerr, system, planet):
    """Log likelihood and gradient"""

    ll = lnprior(p, planet)
    if np.isinf(ll):
        return ll, np.zeros_like(p)

    # Set the coeffs and compute the flux
    set_coeffs(p, planet)
    system.compute(time)

    f0 = system.flux[0]

    # Normalize the model so that the *total* flux baseline is unity
    model = system.flux / f0

    # Compute the chi-squared
    chisq = np.sum((y - model) ** 2 / yerr ** 2) / len(y)
    ll += - 0.5 * chisq

    # Get the derivatives of the flux w/ respect to y
    dfdy = np.array([
        system.gradient['planet1.Y_{1,-1}'],
        system.gradient['planet1.Y_{1,0}'],
        system.gradient['planet1.Y_{1,1}'],
        system.gradient['planet1.L']
    ])

     # Normalization (chain rule)
    '''
    M = np.zeros((len(model), len(model)))
    M[0,:] = system.flux / system.flux[0] ** 2
    dmdf = np.eye(len(model)) / system.flux[0] - M
    dmdy = np.dot(dfdy, dmdf)
    '''
    foo = (dfdy[:, 0] * system.flux.reshape(-1,1)).transpose()
    dmdy = dfdy / f0 - foo / f0 ** 2

    # Now compute the gradient of chi-squared with respect to y
    grad = - 0.5 * 2. * np.dot(dmdy, (model - y) / yerr ** 2.) / len(y)

    return ll, grad

def neglnlike_grad(*args):
    """Negative log likelihood and gradient"""
    ll, gll = lnlike_grad(*args)
    return -ll, -gll

def instatiate_HD189(grad = False):
    """
    Instatiate the HD189733b :class:``starry.Star``, :class:``starry.Planet``
    and :class:``starry.System`` with or without gradients.

    Parameters
    ----------
    grad : bool
        Set to use gradients
    """

    lmax=1
    lambda0=90
    r=0.155313
    L=0.00344
    inc=85.71
    a=8.863
    prot=2.21858
    porb=2.21858
    tref=-2.21858/2.

    if grad:

        # Instantiate the star
        star = starry.grad.Star()

        # Instantiate the planet
        planet = starry.grad.Planet(lmax=lmax,
                                    lambda0=lambda0,
                                    r=r,
                                    L=L,
                                    inc=inc,
                                    a=a,
                                    prot=prot,
                                    porb=porb,
                                    tref=tref)

        # Instantiate the system
        system = starry.grad.System([star, planet])

    else:

        # Instantiate the star
        star = starry.Star()

        # Instantiate the planet
        planet = starry.Planet(lmax=lmax,
                               lambda0=lambda0,
                               r=r,
                               L=L,
                               inc=inc,
                               a=a,
                               prot=prot,
                               porb=porb,
                               tref=tref)

        # Instantiate the system
        system = starry.System([star, planet])

    return star, planet, system

class MaxLikeCartography(object):
    """
    Use ``scipy.optimize.minimize`` to find the maximum likelihood solution

    Parameters
    ----------
    time : array
        Time in days
    y : array
        Relative flux
    yerr : array
        Flux errors
    system : :class:``starry.System``
        System
    planet : :class:``starry.Planet``
        Planet
    N : int
        Number of randomly initialized solutions to find
    jac : bool
        Set to use Jacobians
    """
    def __init__(self, time, y, yerr, system, planet, N = 10, jac = True):

        self.time = time
        self.y = y
        self.yerr = yerr
        self.system = system
        self.planet = planet

        self.N = N
        self.jac = jac

    def compute(self):
        """
        Find the maximum likelihood solution from ``N`` random
        parameter initializations
        """

        if self.jac:
            func = neglnlike_grad
            string = " with gradients"
        else:
            func = neglnlike
            string = ""

        print("Finding maximum likelihood solution%s from %i random parameter initializations..." %(string, self.N))

        results = []
        x0s = []

        # Loop over N finding ML solution from random starting initial state
        for i in tqdm(range(self.N)):

            # Generate a random initial state
            x0 = gen_coeffs_in_bounds(self.planet)

            # Find maximum likelihood solution
            res = minimize(func, x0, args=(self.time, self.y, self.yerr, self.system, self.planet), jac=self.jac)
            #options = {'gtol': 1e-10, 'ftol': 1e-10})

            results.append(res)
            x0s.append(x0)

        # Save input, output pairs
        self.inputs = np.array(x0s)
        self.outputs = np.array([res.fun for res in results])

        # Find best-fitting solution
        ibest = np.argmin(np.array([res.fun for res in results]))

        self.list = results
        self.res = results[ibest]

    def plot_ml_solution(self, data):
        """
        Plot the maximum likelihood solution

        Parameters
        ----------
        data : :class:``EclipseData``
        """

        fig, axs = plt.subplots(1, 2, figsize = (12,5))

        ax= axs[0]
        ax2 = axs[1]

        xx, yy = np.meshgrid(np.linspace(-1, 1, 300), np.linspace(-1, 1, 300))

        set_coeffs(self.res.x, self.planet)

        img = [self.planet.map.evaluate(x=xx[j], y=yy[j], theta = 0) for j in range(300)]
        ax.imshow(img, origin="lower",
                     interpolation="none", cmap="plasma",
                     extent=(-1, 1, -1, 1))
        ax.contour(img, origin="lower",
                      extent=(-1, 1, -1, 1),
                      colors='k', linewidths=1)
        ax.set_frame_on(False)
        ax.set_xticks([])
        ax.set_yticks([])
        expr = r"${0}$".format(self.planet.map.__repr__()[12:-1])
        #expr = "%.3e" %res.fun
        ax.set_xlabel(expr, fontsize=12)


        self.system.compute(self.time)

        # Plot the true model and our noised data
        ax2.plot(self.time, self.system.flux / self.system.flux[0], '-', color='C1', label = "model")
        ax2.plot(data.df_med['time'], data.df_med['flux'], label = "rolling median")

        ax2.set_xlabel('Time [days]', fontsize=14, fontweight='bold');
        ax2.set_ylabel('Normalized Flux', fontsize=14, fontweight='bold');
        ax2.set_ylim(0.995, 1.002)
        ax2.set_xlim(data.time.min(), data.time.max())

    def plot_all_solutions(self, data):
        """
        Plot all of the maximum likelihood solutions for ``N`` attempts

        Parameters
        ----------
        data : :class:``EclipseData``
        """

        for res in self.list:

            fig, axs = plt.subplots(1, 2, figsize = (12,5))

            ax= axs[0]
            ax2 = axs[1]

            xx, yy = np.meshgrid(np.linspace(-1, 1, 300), np.linspace(-1, 1, 300))

            set_coeffs(res.x, self.planet)

            img = [self.planet.map.evaluate(x=xx[j], y=yy[j], theta = 0) for j in range(300)]
            ax.imshow(img, origin="lower",
                         interpolation="none", cmap="plasma",
                         extent=(-1, 1, -1, 1))
            ax.contour(img, origin="lower",
                          extent=(-1, 1, -1, 1),
                          colors='k', linewidths=1)
            ax.set_frame_on(False)
            ax.set_xticks([])
            ax.set_yticks([])
            expr = r"${0}$".format(self.planet.map.__repr__()[12:-1])
            ax.set_xlabel(expr, fontsize=12)


            self.system.compute(data.time)

            # Plot the true model and our noised data
            ax2.plot(data.time, self.system.flux / self.system.flux[0], '-', color='C1', label = "model")
            ax2.plot(data.df_med['time'], data.df_med['flux'], label = "rolling median")

            ax2.set_xlabel('Time [days]', fontsize=14, fontweight='bold');
            ax2.set_ylabel('Normalized Flux', fontsize=14, fontweight='bold');
            ax2.set_ylim(0.995, 1.002)
            ax2.set_xlim(data.time.min(), data.time.max())
            ax2.set_title("%.3e" %res.fun)

class MCMCCartography(object):
    """
    Use ``starry`` with ``emcee``.

    Parameters
    ----------
    time : array
        Time in days
    y : array
        Relative flux
    yerr : array
        Flux errors
    system : :class:``starry.System``
        System
    planet : :class:``starry.Planet``
        Planet
    p0 : list
        Starting chain positions
    chain : numpy.ndarray
        MCMC chain
    chain_path : str
        Location of MCMC chain to be loaded or saved
    labels : list
        List of free parameter labels
    """
    def __init__(self, time, y, yerr, system, planet, p0 = None,
                 chain = None, chain_path = "map_chains.npz",
                 labels = np.array([r"$Y_{1,-1}$", r"$Y_{1,0}$", r"$Y_{1,1}$", r"$L$"])):
        self.time = time
        self.y = y
        self.yerr = yerr
        self.system = system
        self.planet = planet

        self.p0 = p0
        self.chain = chain
        self.chain_path = chain_path
        self.labels = labels

        if self.chain_path is not None:
            self.load_chain()

    def run_mcmc(self, nsteps = 1000):
        """Run an MCMC chain for ``nsteps``"""

        self.nsteps = nsteps
        self.ndim = len(self.labels)
        self.nwalk = 10 * self.ndim

        # Initialize walkers *in bounds*, if not provided
        if self.p0 is None:
            self.p0 = []
            self.p0 = np.array([self.p0.append(gen_coeffs_in_bounds(self.planet)) for i in range(self.nwalk)])
        else:
            self.nwalk = self.p0.shape[0]

        # Run the MCMC chain
        if not hasattr(self, "sampler"):
            # Create a new sampler
            self.sampler = emcee.EnsembleSampler(self.nwalk, self.ndim, lnlike,
                                    args=[self.time, self.y, self.yerr, self.system, self.planet])
            for i in tqdm(self.sampler.sample(self.p0, iterations=self.nsteps), total=self.nsteps):
                pass
        else:
            # Continue running existing sampler from last step in chain
            for i in tqdm(self.sampler.sample(self.sampler.chain[:,-1,:], iterations=self.nsteps), total=self.nsteps):
                pass

        # Save sampler.chain as chain (this is might be really stupid b/c it duplicates
        # the large chains)
        self.chain = self.sampler.chain

        return

    def save_chain(self):
        """Save MCMC chain"""

        if not os.path.exists(self.chain_path):
            np.savez(self.chain_path, chain=self.sampler.chain)
        else:
            print("Saved file already exists with this name. Please specify\
                  a different `chain_path`.")

        return

    def load_chain(self):
        """Load MCMC chain from a saved file"""

        if os.path.exists(self.chain_path):
            print("Loading MCMC chains from a saved state...")
            loaded_chain = np.load(self.chain_path)
            self.chain = loaded_chain['chain']
            self.ndim = len(self.labels)
            self.nwalk = 10 * self.ndim

        return

    def plot_trace(self, nburn = 0):
        """Plots the MCMC chains for manual inspection"""

        ndim = self.ndim
        nwalk = self.nwalk
        labels = self.labels

        # Plot the chains w/out the burn-in
        fig = plt.figure(figsize=(16, 8))
        fig.subplots_adjust(bottom=0.05, top=0.95, hspace=0.1)
        axc = [plt.subplot2grid((ndim, 10), (n, 0), colspan=8, rowspan=1)
               for n in range(ndim)]
        axh = [plt.subplot2grid((ndim, 10), (n, 8), colspan=2,
                               rowspan=1, sharey=axc[n]) for n in range(ndim)]
        alpha = 0.3
        for i, label in enumerate(labels):
            for k in range(nwalk):
                axc[i].plot(self.chain[k, nburn:, i], alpha=alpha, lw=1)
                axc[i].set_ylabel(label, fontsize=24)
            axh[i].hist(self.chain[:, nburn:, i].flatten(), bins=30,
                        orientation="horizontal", histtype='step',
                        fill=False, color='k', lw=1)
            plt.setp(axh[i].get_yticklabels(), visible=False)
            plt.setp(axh[i].get_xticklabels(), visible=False)
            #axh[i].axhline(maxlike[i], color='C0')

        """
        for k in range(nwalk):
            axc[-1].plot(sampler.lnprobability[k, nburn:], alpha=alpha, lw=1)
            axc[-1].set_ylabel("lnlike", fontsize=24);
        """
        plt.setp(axh[-1].get_yticklabels(), visible=False)
        plt.setp(axh[-1].get_xticklabels(), visible=False);

        self.fig_trace = fig

        return

    def apply_burnin(self, nburn = 0):
        """Creates attribute `samples` by flattening chain beyond the burn-in `nburn`"""
        self.samples = self.chain[:, nburn:, :].reshape(-1, self.ndim)
        return

    def get_hot_spot_samples(self):

        # Calculate the latitude and longitude of the hotspot offset
        lat, lon = np.zeros(self.samples.shape[0]), np.zeros(self.samples.shape[0])
        for i in range(self.samples.shape[0]):
            lat[i], lon[i] = hotspot_offset(self.samples[i,:])

        # Append lat and lon samples to the chains
        self.samples2 = np.vstack([self.samples.T, lat, lon]).T
        self.labels2 = np.hstack([self.labels, r"$\check{\theta}$", r"$\check{\phi}$"])

        return

    def plot_corner_with_map(self):

        medvals = np.nanmedian(self.samples, axis=0)
        truths = np.nanmedian(self.samples2, axis=0)

        fig = corner.corner(self.samples2, labels=self.labels2, bins=45, show_titles=True, title_fmt='.3f');
        for ax in fig.axes:
            ax.xaxis.label.set_fontsize(20)
            ax.yaxis.label.set_fontsize(20)
            ax.title.set_fontsize(20)

        left = 0.55
        bottom = 0.55
        width = 0.45
        height = 0.45
        ax = fig.add_axes([left, bottom, width, height])

        xx, yy = np.meshgrid(np.linspace(-1, 1, 300), np.linspace(-1, 1, 300))

        set_coeffs(medvals, self.planet)
        img = [self.planet.map.evaluate(x=xx[j], y=yy[j], theta = 0) for j in range(300)]
        ax.imshow(img, origin="lower",
                     interpolation="none", cmap="plasma",
                     extent=(-1, 1, -1, 1))
        ax.contour(img, origin="lower",
                      extent=(-1, 1, -1, 1),
                      colors='k', linewidths=1)
        ax.set_frame_on(False)
        ax.set_xticks([])
        ax.set_yticks([])
        #expr = r"${0}$".format(planet.map.__repr__()[12:-1])
        #ax.set_xlabel(expr, fontsize=12)
        #ax.set_title("max likelihood map", fontsize=24, fontweight='bold');

        self.fig_corner = fig
        return

    def plot_fit(self, data):

        # Use median values of chains
        medians = np.nanmedian(self.samples, axis=0)

        # Calculate light curve for median params
        set_coeffs(medians, self.planet)
        self.system.compute(self.time)

        # Plot the true model and our noised data
        fig, ax = plt.subplots(1, figsize=(16, 8))
        ax.plot(self.time, self.y, "o", alpha = 1., ms = 0.1, color='C0', label = "data")
        ax.plot(self.time, self.system.flux / self.system.flux[0], '-', color='C1', label = "model")
        ax.plot(data.df_med['time'], data.df_med['flux'], label = "rolling median")

        ax.set_xlabel('Time [days]', fontsize=14, fontweight='bold');
        ax.set_ylabel('Normalized Flux', fontsize=14, fontweight='bold');
        ax.set_ylim(0.995, 1.002)
        ax.set_xlim(self.time.min(), self.time.max())
        ax.legend();

        self.fig_fit = fig

        return

    def plot_fit_full(self, data):

        # Use median values of chains
        medians = np.nanmedian(self.samples, axis=0)

        # Calculate light curve for median params
        set_coeffs(medians, self.planet)
        self.system.compute(self.time)

        # Plot the true model and our noised data
        fig = plt.figure(figsize=(8,10))
        gs = gridspec.GridSpec(3,1, height_ratios = [0.3, 0.03, 0.5])
        fig.subplots_adjust(wspace=0.08, hspace=0.00)
        ax = plt.subplot(gs[0])
        ax2 = plt.subplot(gs[2])

        ax.plot(data.df['time'], data.df['flux'], "o", alpha = 0.25, ms = 0.1, color='C0', zorder = -1)
        ax.plot(data.df_med['time'], data.df_med['flux'], label = "data w/ rolling median", zorder = 10, color = "C0")

        ax.set_xlabel('Time [days]', fontsize=14, fontweight='bold');
        ax.set_ylabel('Normalized Flux', fontsize=14, fontweight='bold');
        ax.axvline(self.time.min(), color = "grey", lw = 0.5)
        ax.axvline(self.time.max(), color = "grey", lw = 0.5)

        ax.set_ylim(0.975, 1.025)
        ax.set_xlim(data.df['time'].min(), data.df['time'].max())

        ax2.plot(self.time, self.y, "o", alpha = 0.5, ms = 0.1, color='C0', zorder = -1)
        ax2.plot(data.df_med['time'], data.df_med['flux'], label = "data w/ rolling median", zorder = 10, color = "C0")
        ax2.plot(self.time, self.system.flux / self.system.flux[0], '-', color='C1', label = "max likelihood model")

        ax2.set_xlabel('Time [days]', fontsize=14, fontweight='bold');
        ax2.set_ylabel('Normalized Flux', fontsize=14, fontweight='bold');

        # Mask phasecurve
        ax2.set_xlim(self.time.min(), self.time.max())
        ax2.set_ylim(0.995, 1.002)
        ax2.legend(numpoints = 3);

        ax.set_rasterization_zorder(0)
        ax2.set_rasterization_zorder(0)

        self.fig_fit_full = fig

        return

if __name__ == "__main__":

    chain_path = "map_chain.npz"             # Name of file with MCMC chain
    grad = True                              # Use gradients in ML fit(s)
    N = 1                                    # Number of ML fits
    nsteps = 1000                            # Number of MCMC steps
    nwalk = 40                               # Number of MCMC walkers
    std_ball = [0.01, 0.01, 0.01, 0.001]     # Gaussian ball for MCMC p0

    # Get HD189 data
    data = EclipseData(plot = False)

    # If there are no saved chains in this path
    if not os.path.exists(chain_path):

        # Find ML solution
        # Initialize system
        star, planet, system = instatiate_HD189(grad = grad)
        results = MaxLikeCartography(data.time, data.y, data.yerr, system, planet, N = N, jac = grad)
        results.compute()

        # Initialize system *without gradients*
        star, planet, system = instatiate_HD189(grad = False)

        # Initialize MCMC walkers *from maximum likelihood optimized solution*
        p0 = emcee.utils.sample_ball(results.res.x, std_ball, nwalk)

        # Run MCMC
        mcmc = MCMCCartography(data.time, data.y, data.yerr, system, planet, p0 = p0,
                                     chain_path = chain_path)
        mcmc.run_mcmc(nsteps=nsteps)

    else:

        # Initialize system *without gradients*
        star, planet, system = instatiate_HD189(grad = False)

        # Read-in saved chain
        mcmc = MCMCCartography(data.time, data.y, data.yerr, system, planet,
                                     chain_path = chain_path)

    # Plot the chains as a function of iteration
    #mcmc.plot_trace()

    # Apply a burn-in cut to samples
    mcmc.apply_burnin(nburn = 2000)

    # Plot the fit to the data
    #mcmc.plot_fit(data)

    # Plot the full lightcurve and the fit to the eclipse
    mcmc.plot_fit_full(data)
    mcmc.fig_fit_full.savefig("hd189_mcmc_fit.pdf", bbox_inches = "tight")

    # Get hot spot offset samples and append to a newly created chain
    mcmc.get_hot_spot_samples()

    # Plot the corner with the map in the upper right
    mcmc.plot_corner_with_map()
    mcmc.fig_corner.savefig("hd189_mcmc_corner.pdf", bbox_inches = "tight")

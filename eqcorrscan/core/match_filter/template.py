"""
Functions for network matched-filter detection of seismic data.

Designed to cross-correlate templates generated by template_gen function
with data and output the detections.

:copyright:
    EQcorrscan developers.

:license:
    GNU Lesser General Public License, Version 3
    (https://www.gnu.org/copyleft/lesser.html)
"""
import copy
import getpass
import os
import re
import shutil
import logging

import numpy as np
from obspy import Stream
from obspy.core.event import Comment, Event, CreationInfo

from eqcorrscan.core.match_filter.helpers import _test_event_similarity
from eqcorrscan.core.match_filter.matched_filter import (
    _group_detect, MatchFilterError)
from eqcorrscan.core import template_gen

Logger = logging.getLogger(__name__)


class Template(object):
    """
    Template object holder.

    Contains waveform data and metadata parameters used to generate the
    template.

    :type name: str
    :param name:
        A name associated with the template as a parsable string (no spaces
        allowed)
    :type st: `obspy.core.stream.Stream`
    :param st:
        The Stream object containing the Template waveform data
    :type lowcut: float
    :param lowcut:
        The low-cut filter used to achieve st (float, Hz)
    :type highcut: float
    :param highcut:
        The high-cut filter used to achieve st (float, Hz)
    :type samp_rate: float
    :param samp_rate:
        The Sampling-rate of the template (float, Hz) - note that this should
        be the same as the sampling-rate of all traces in st.
    :type filt_order: int
    :param filt_order:
        The order of the filter applied to achieve st (int), see pre-processing
        functions for a more complete description
    :type process_length: float
    :param process_length:
        The length of data (in seconds) processed to achieve st. e.g. if you
        processed a day of data then cut the template st from this, then
        process_length would be 86400.0 (float)
    :type prepick: float
    :param prepick:
        The time before picks that waveforms were cut to make st
        (seconds, float)
    :type event: `obspy.core.event.Event`
    :param event:
        The Event associated with the template (obspy.core.event.Event)
    """

    def __init__(self, name=None, st=None, lowcut=None, highcut=None,
                 samp_rate=None, filt_order=None, process_length=None,
                 prepick=None, event=None):
        name_regex = re.compile(r"^[-A-Za-z_0-9]+$")
        if name is not None and not re.match(name_regex, name):
            raise ValueError("Invalid name: '%s' - Must satisfy the regex "
                             "'%s'." % (name, name_regex.pattern))
        if name is None:
            temp_name = "unnamed"
        else:
            temp_name = name
        self.name = name
        self.st = st
        self.lowcut = lowcut
        self.highcut = highcut
        self.samp_rate = samp_rate
        if st and samp_rate is not None:
            for tr in st:
                if not tr.stats.sampling_rate == self.samp_rate:
                    raise MatchFilterError(
                        'Sampling rates do not match in data.')
        self.filt_order = filt_order
        self.process_length = process_length
        self.prepick = prepick
        if event is not None:
            if "eqcorrscan_template_" + temp_name not in \
                    [c.text for c in event.comments]:
                event.comments.append(Comment(
                    text="eqcorrscan_template_" + temp_name,
                    creation_info=CreationInfo(agency='eqcorrscan',
                                               author=getpass.getuser())))
        self.event = event

    def __repr__(self):
        """
        Print the template.


        .. rubric:: Example

        >>> from obspy import read
        >>> print(Template())
        Template()
        >>> template = Template(
        ...     name='a', st=read(), lowcut=2.0, highcut=8.0, samp_rate=100,
        ...     filt_order=4, process_length=3600, prepick=0.5)
        >>> print(template) # doctest: +NORMALIZE_WHITESPACE
        Template a:
             3 channels;
             lowcut: 2.0 Hz;
             highcut: 8.0 Hz;
             sampling rate 100 Hz;
             filter order: 4;
             process length: 3600 s

        """
        if self.name is None:
            return 'Template()'
        if self.st is None:
            st_len = 0
        else:
            st_len = len(self.st)
        print_str = ('Template %s: \n\t %s channels;\n\t lowcut: %s Hz;'
                     '\n\t highcut: %s Hz;\n\t sampling rate %s Hz;'
                     '\n\t filter order: %s; \n\t process length: %s s'
                     % (self.name, st_len, self.lowcut, self.highcut,
                        self.samp_rate, self.filt_order, self.process_length))
        return print_str

    def __eq__(self, other, verbose=False, shallow_event_check=False):
        """
        Check for Template equality, rich comparison operator '=='

        .. rubric:: Example

        >>> from obspy import read
        >>> template_a = Template(
        ...     name='a', st=read(), lowcut=2.0, highcut=8.0, samp_rate=100,
        ...     filt_order=4, process_length=3600, prepick=0.5)
        >>> template_b = template_a.copy()
        >>> template_a == template_b
        True


        This will check all parameters of template including the data in the
        stream.

        >>> template_b.st[0].data = template_b.st[0].data[0:-1]
        >>> template_a == template_b
        False

        >>> template_b = template_a.copy()
        >>> template_b.st[0].stats.station = 'MIS'
        >>> template_a == template_b
        False

        >>> template_b = template_a.copy()
        >>> template_b.st = template_b.st[0]
        >>> template_a == template_b
        False

        >>> template_b = template_a.copy()
        >>> template_b.lowcut = 5.0
        >>> template_a == template_b
        False


        This will also test if the events in the templates are the same,
        ignoring resource ID's as obspy will alter these so that they do not
        match.

        >>> from eqcorrscan.core.match_filter.party import Party
        >>> party = Party().read()
        >>> template_a = party.families[0].template
        >>> template_b = template_a.copy()
        >>> template_b.event.origins[0].time = \
            template_a.event.origins[0].time + 20
        >>> template_a == template_b
        False
        """
        for key in self.__dict__.keys():
            if key == 'st':
                self_is_stream = isinstance(self.st, Stream)
                other_is_stream = isinstance(other.st, Stream)
                if self_is_stream and other_is_stream:
                    for tr, oth_tr in zip(self.st.sort(),
                                          other.st.sort()):
                        if not np.array_equal(tr.data, oth_tr.data):
                            if verbose:
                                print("Template data are not equal on "
                                      "{0}".format(tr.id))
                            return False
                        for trkey in ['network', 'station', 'channel',
                                      'location', 'starttime', 'endtime',
                                      'sampling_rate', 'delta', 'npts',
                                      'calib']:
                            if tr.stats[trkey] != oth_tr.stats[trkey]:
                                if verbose:
                                    print("{0} does not match in "
                                          "template".format(trkey))
                                return False
                elif self_is_stream and not other_is_stream:
                    return False
                elif not self_is_stream and other_is_stream:
                    return False
            elif key == 'event':
                self_is_event = isinstance(self.event, Event)
                other_is_event = isinstance(other.event, Event)
                if self_is_event and other_is_event:
                    if not _test_event_similarity(
                            self.event, other.event, verbose=verbose,
                            shallow=shallow_event_check):
                        return False
                elif self_is_event and not other_is_event:
                    return False
                elif not self_is_event and other_is_event:
                    return False
            elif not self.__dict__[key] == other.__dict__[key]:
                return False
        return True

    def __ne__(self, other):
        """
        Rich comparison operator '!='.

        .. rubric:: Example

        >>> from obspy import read
        >>> template_a = Template(
        ...     name='a', st=read(), lowcut=2.0, highcut=8.0, samp_rate=100,
        ...     filt_order=4, process_length=3600, prepick=0.5)
        >>> template_b = template_a.copy()
        >>> template_a != template_b
        False
        """
        return not self.__eq__(other)

    def copy(self):
        """
        Returns a copy of the template.

        :return: Copy of template

        .. rubric:: Example

        >>> from obspy import read
        >>> template_a = Template(
        ...     name='a', st=read(), lowcut=2.0, highcut=8.0, samp_rate=100,
        ...     filt_order=4, process_length=3600, prepick=0.5)
        >>> template_b = template_a.copy()
        >>> template_a == template_b
        True
        """
        return copy.deepcopy(self)

    def same_processing(self, other):
        """
        Check is the templates are processed the same.

        .. rubric:: Example

        >>> from obspy import read
        >>> template_a = Template(
        ...     name='a', st=read(), lowcut=2.0, highcut=8.0, samp_rate=100,
        ...     filt_order=4, process_length=3600, prepick=0.5)
        >>> template_b = template_a.copy()
        >>> template_a.same_processing(template_b)
        True
        >>> template_b.lowcut = 5.0
        >>> template_a.same_processing(template_b)
        False
        """
        for key in self.__dict__.keys():
            if key in ['name', 'st', 'prepick', 'event', 'template_info']:
                continue
            if not self.__dict__[key] == other.__dict__[key]:
                return False
        return True

    def write(self, filename, format='tar'):
        """
        Write template.

        :type filename: str
        :param filename:
            Filename to write to, if it already exists it will be opened and
            appended to, otherwise it will be created.
        :type format: str
        :param format:
            Format to write to, either 'tar' (to retain metadata), or any obspy
            supported waveform format to just extract the waveform.

        .. rubric:: Example

        >>> from obspy import read
        >>> template_a = Template(
        ...     name='a', st=read(), lowcut=2.0, highcut=8.0, samp_rate=100,
        ...     filt_order=4, process_length=3600, prepick=0.5)
        >>> template_a.write('test_template') # doctest: +NORMALIZE_WHITESPACE
        Template a:
         3 channels;
         lowcut: 2.0 Hz;
         highcut: 8.0 Hz;
         sampling rate 100 Hz;
         filter order: 4;
         process length: 3600 s
        >>> template_a.write('test_waveform.ms',
        ...                  format='MSEED') # doctest: +NORMALIZE_WHITESPACE
        Template a:
         3 channels;
         lowcut: 2.0 Hz;
         highcut: 8.0 Hz;
         sampling rate 100 Hz;
         filter order: 4;
         process length: 3600 s
        """
        from eqcorrscan.core.match_filter.tribe import Tribe

        if format == 'tar':
            Tribe(templates=[self]).write(filename=filename)
        else:
            self.st.write(filename, format=format)
        return self

    def read(self, filename):
        """
        Read template from tar format with metadata.

        :type filename: str
        :param filename: Filename to read template from.

        .. rubric:: Example

        >>> from obspy import read
        >>> template_a = Template(
        ...     name='a', st=read(), lowcut=2.0, highcut=8.0, samp_rate=100,
        ...     filt_order=4, process_length=3600, prepick=0.5)
        >>> template_a.write(
        ...     'test_template_read') # doctest: +NORMALIZE_WHITESPACE
        Template a:
         3 channels;
         lowcut: 2.0 Hz;
         highcut: 8.0 Hz;
         sampling rate 100 Hz;
         filter order: 4;
         process length: 3600 s
        >>> template_b = Template().read('test_template_read.tgz')
        >>> template_a == template_b
        True
        """
        from eqcorrscan.core.match_filter.tribe import Tribe

        tribe = Tribe()
        tribe.read(filename=filename)
        if len(tribe) > 1:
            raise IOError('Multiple templates in file')
        for key in self.__dict__.keys():
            self.__dict__[key] = tribe[0].__dict__[key]
        return self

    def detect(self, stream, threshold, threshold_type, trig_int,
               plot=False, plotdir=None, pre_processed=False, daylong=False,
               parallel_process=True, xcorr_func=None, concurrency=None,
               cores=None, ignore_length=False, overlap="calculate",
               full_peaks=False, **kwargs):
        """
        Detect using a single template within a continuous stream.

        :type stream: `obspy.core.stream.Stream`
        :param stream: Continuous data to detect within using the Template.
        :type threshold: float
        :param threshold:
            Threshold level, if using `threshold_type='MAD'` then this will be
            the multiple of the median absolute deviation.
        :type threshold_type: str
        :param threshold_type:
            The type of threshold to be used, can be MAD, absolute or
            av_chan_corr.  See Note on thresholding below.
        :type trig_int: float
        :param trig_int:
            Minimum gap between detections in seconds. If multiple detections
            occur within trig_int of one-another, the one with the highest
            cross-correlation sum will be selected.
        :type plot: bool
        :param plot: Turn plotting on or off.
        :type plotdir: str
    ￼	:param plotdir:
            The path to save plots to. If `plotdir=None` (default) then the
            figure will be shown on screen.
        :type pre_processed: bool
        :param pre_processed:
            Set to True if `stream` has already undergone processing, in this
            case eqcorrscan will only check that the sampling rate is correct.
            Defaults to False, which will use the
            :mod:`eqcorrscan.utils.pre_processing` routines to resample and
            filter the continuous data.
        :type daylong: bool
        :param daylong:
            Set to True to use the
            :func:`eqcorrscan.utils.pre_processing.dayproc` routine, which
            preforms additional checks and is more efficient for day-long data
            over other methods.
        :type parallel_process: bool
        :param parallel_process:
        :type xcorr_func: str or callable
        :param xcorr_func:
            A str of a registered xcorr function or a callable for implementing
            a custom xcorr function. For more details see
            :func:`eqcorrscan.utils.correlate.register_array_xcorr`.
        :type concurrency: str
        :param concurrency:
            The type of concurrency to apply to the xcorr function. Options are
            'multithread', 'multiprocess', 'concurrent'. For more details see
            :func:`eqcorrscan.utils.correlate.get_stream_xcorr`.
        :type cores: int
        :param cores: Number of workers for processing and detection.
        :type ignore_length: bool
        :param ignore_length:
            If using daylong=True, then dayproc will try check that the data
            are there for at least 80% of the day, if you don't want this check
            (which will raise an error if too much data are missing) then set
            ignore_length=True.  This is not recommended!
        :type overlap: float
        :param overlap:
            Either None, "calculate" or a float of number of seconds to
            overlap detection streams by.  This is to counter the effects of
            the delay-and-stack in calculating cross-correlation sums. Setting
            overlap = "calculate" will work out the appropriate overlap based
            on the maximum lags within templates.
        :type full_peaks: bool
        :param full_peaks:
            See :func:`eqcorrscan.utils.findpeaks.find_peaks2_short`

        :returns: Family of detections.


        .. Note::
            When using the "fftw" correlation backend the length of the fft
            can be set. See :mod:`eqcorrscan.utils.correlate` for more info.

        .. Note::
            `stream` must not be pre-processed. If your data contain gaps
            you should *NOT* fill those gaps before using this method.
            The pre-process functions (called within) will fill the gaps
            internally prior to processing, process the data, then re-fill
            the gaps with zeros to ensure correlations are not incorrectly
            calculated within gaps. If your data have gaps you should pass a
            merged stream without the `fill_value` argument
            (e.g.: `stream = stream.merge()`).

        .. note::
            **Data overlap:**

            Internally this routine shifts and trims the data according to
            the offsets in the template (e.g. if trace 2 starts 2 seconds
            after trace 1 in the template then the continuous data will be
            shifted by 2 seconds to align peak correlations prior to summing).
            Because of this, detections at the start and end of continuous data
            streams **may be missed**.  The maximum time-period that might be
            missing detections is the maximum offset in the template.

            To work around this, if you are conducting matched-filter
            detections through long-duration continuous data, we suggest using
            some overlap (a few seconds, on the order of the maximum offset
            in the templates) in the continous data.  You will then need to
            post-process the detections (which should be done anyway to remove
            duplicates).

        .. note::
            **Thresholding:**

            **MAD** threshold is calculated as the:

            .. math::

                threshold {\\times} (median(abs(cccsum)))

            where :math:`cccsum` is the cross-correlation sum for a
            given template.

            **absolute** threshold is a true absolute threshold based on the
            cccsum value.

            **av_chan_corr** is based on the mean values of single-channel
            cross-correlations assuming all data are present as required
            for the template, e.g:

            .. math::

                av\_chan\_corr\_thresh=threshold \\times (cccsum /
                len(template))

            where :math:`template` is a single template from the input and the
            length is the number of channels within this template.

        .. Note::
            See tutorials for example.
        """
        if kwargs.get("plotvar") is not None:
            Logger.warning("plotvar is depreciated, use plot instead")
            plot = kwargs.get("plotvar")
        party = _group_detect(
            templates=[self], stream=stream.copy(), threshold=threshold,
            threshold_type=threshold_type, trig_int=trig_int, plotdir=plotdir,
            plot=plot, pre_processed=pre_processed, daylong=daylong,
            parallel_process=parallel_process, xcorr_func=xcorr_func,
            concurrency=concurrency, cores=cores, ignore_length=ignore_length,
            overlap=overlap, full_peaks=full_peaks, **kwargs)
        return party[0]

    def construct(self, method, name, lowcut, highcut, samp_rate, filt_order,
                  length, prepick, swin="all", process_len=86400,
                  all_horiz=False, delayed=True, plot=False, plotdir=None,
                  min_snr=None, parallel=False, num_cores=False,
                  skip_short_chans=False, **kwargs):
        """
        Construct a template using a given method.

        :param method:
            Method to make the template, the only available method is:
            `from_sac`. For all other methods (`from_seishub`, `from_client`
            and `from_meta_file`) use `Tribe.construct()`.
        :type method: str
        :type name: str
        :param name: Name for the template
        :type lowcut: float
        :param lowcut:
            Low cut (Hz), if set to None will not apply a lowcut
        :type highcut: float
        :param highcut:
            High cut (Hz), if set to None will not apply a highcut.
        :type samp_rate: float
        :param samp_rate:
            New sampling rate in Hz.
        :type filt_order: int
        :param filt_order:
            Filter level (number of corners).
        :type length: float
        :param length: Length of template waveform in seconds.
        :type prepick: float
        :param prepick: Pre-pick time in seconds
        :type swin: str
        :param swin:
            P, S, P_all, S_all or all, defaults to all: see note in
            :func:`eqcorrscan.core.template_gen.template_gen`
        :type process_len: int
        :param process_len: Length of data in seconds to download and process.
        :type all_horiz: bool
        :param all_horiz:
            To use both horizontal channels even if there is only a pick on
            one of them.  Defaults to False.
        :type delayed: bool
        :param delayed: If True, each channel will begin relative to it's own
            pick-time, if set to False, each channel will begin at the same
            time.
        :type plot: bool
        :param plot: Plot templates or not.
        :type plotdir: str
    ￼	:param plotdir:
            The path to save plots to. If `plotdir=None` (default) then the
            figure will be shown on screen.
        :type min_snr: float
        :param min_snr:
            Minimum signal-to-noise ratio for a channel to be included in the
            template, where signal-to-noise ratio is calculated as the ratio
            of the maximum amplitude in the template window to the rms
            amplitude in the whole window given.
        :type parallel: bool
        :param parallel: Whether to process data in parallel or not.
        :type num_cores: int
        :param num_cores:
            Number of cores to try and use, if False and parallel=True,
            will use either all your cores, or as many traces as in the data
            (whichever is smaller).
        :type skip_short_chans: bool
        :param skip_short_chans:
            Whether to ignore channels that have insufficient length data or
            not. Useful when the quality of data is not known, e.g. when
            downloading old, possibly triggered data from a datacentre

        .. note::

            `method=from_sac` requires the following kwarg(s):
            :param list sac_files:
                osbpy.core.stream.Stream of sac waveforms, or list of paths to
                sac waveforms.
            .. note::
                See `eqcorrscan.utils.sac_util.sactoevent` for details on
                how pick information is collected.

        .. rubric:: Example

        >>> # Get the path to the test data
        >>> import eqcorrscan
        >>> import os, glob
        >>> TEST_PATH = (
        ...     os.path.dirname(eqcorrscan.__file__) + '/tests/test_data')
        >>> sac_files = glob.glob(TEST_PATH + '/SAC/2014p611252/*')
        >>> template = Template().construct(
        ...     method='from_sac', name='test', lowcut=2.0, highcut=8.0,
        ...     samp_rate=20.0, filt_order=4, prepick=0.1, swin='all',
        ...     length=2.0, sac_files=sac_files)
        >>> print(template) # doctest: +NORMALIZE_WHITESPACE
        Template test:
         12 channels;
         lowcut: 2.0 Hz;
         highcut: 8.0 Hz;
         sampling rate 20.0 Hz;
         filter order: 4;
         process length: 300.0 s


        This will raise an error if the method is unsupported:

        >>> template = Template().construct(
        ...     method='from_meta_file', name='test', lowcut=2.0, highcut=8.0,
        ...     samp_rate=20.0, filt_order=4, prepick=0.1, swin='all',
        ...     length=2.0) # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        NotImplementedError: Method is not supported, use \
        Tribe.construct instead.

        """
        if method in ['from_meta_file', 'from_seishub', 'from_client',
                      'multi_template_gen']:
            raise NotImplementedError('Method is not supported, '
                                      'use Tribe.construct instead.')
        streams, events, process_lengths = template_gen.template_gen(
            method=method, lowcut=lowcut, highcut=highcut, length=length,
            filt_order=filt_order, samp_rate=samp_rate, prepick=prepick,
            return_event=True, swin=swin, process_len=process_len,
            all_horiz=all_horiz, delayed=delayed, plot=plot, plotdir=plotdir,
            min_snr=min_snr, parallel=parallel, num_cores=num_cores,
            skip_short_chans=skip_short_chans, **kwargs)
        self.name = name
        st = streams[0]
        event = events[0]
        process_length = process_lengths[0]
        for tr in st:
            if not np.any(tr.data.astype(np.float16)):
                Logger.warning('Data are zero in float16, missing data,'
                               ' will not use: {0}'.format(tr.id))
                st.remove(tr)
        self.st = st
        self.lowcut = lowcut
        self.highcut = highcut
        self.filt_order = filt_order
        self.samp_rate = samp_rate
        self.process_length = process_length
        self.prepick = prepick
        self.event = event
        return self


def read_template(fname):
    """
    Read a Template object from a tar archive.

    :type fname: str
    :param fname: Filename to read from

    :return: :class:`eqcorrscan.core.match_filter.Template`
    """
    template = Template()
    template.read(filename=fname)
    return template


def group_templates(templates):
    """
    Group templates into sets of similarly processed templates.

    :type templates: List of Tribe of Templates
    :return: List of Lists of Templates.
    """
    template_groups = []
    for master in templates:
        for group in template_groups:
            if master in group:
                break
        else:
            new_group = [master]
            for slave in templates:
                if master.same_processing(slave) and master != slave:
                    new_group.append(slave)
            template_groups.append(new_group)
    # template_groups will contain an empty first list
    for group in template_groups:
        if len(group) == 0:
            template_groups.remove(group)
    return template_groups


if __name__ == "__main__":
    import doctest

    doctest.testmod()
    # List files to be removed after doctest
    cleanup = ['test_template.tgz', 'test_waveform.ms']
    for f in cleanup:
        if os.path.isfile(f):
            os.remove(f)
        elif os.path.isdir(f):
            shutil.rmtree(f)

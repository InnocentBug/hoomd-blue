from collections.abc import ABCMeta, abstractmethod
from math import log10
from sys import stdout

from hoomd.analyze.custom_analyzer import _InternalCustomAnalyzer
from hoomd.custom.custom_action import _InternalCustomAction
from hoomd.parameterdicts import ParameterDict
from hoomd.typeconverter import OnlyType
from hoomd.util import dict_flatten


class _OutputWriter(metaclass=ABCMeta):
    """Represents the necessary functions for writing out data.

    We use this to ensure the output object passed to CSV will support the
    necessary functions.
    """
    @abstractmethod
    def flush(self):
        pass

    @abstractmethod
    def write(self):
        pass

    @abstractmethod
    @property
    def mode(self):
        pass


class _Formatter:
    """Internal class for number and string formatting for CSV object.

    Main method is ``__call__``. It takes a value with the corresponding column
    width and outputs the string to use for that column. Some of these
    parameters are not currently used in the _InternalCSV class, but are
    available in the _Formatter class, meaning that adding these features later
    would be fairly simple. I (Brandon Butler) did not think they were worth
    complicating the CSV Logger any more than it currently is though, so they
    are not used now.

    Args:
        pretty (bool): whether to attempt to make output pretty (more readable).
        max_precision (int): The max length for formatting a number or string.
        max_decimals_pretty (int): The maximum number of decimals. This is
            required to ensure that the decimals don't eat up all space in a
            pretty print.
        pad (str, optional): What to pad extra column space with, defaults to
            space.
        align (str, optional): What type of alignment to use, defaults to
            centered ('^').
    """

    def __init__(self, pretty=True,
                 max_precision=15, max_decimals_pretty=5,
                 pad=" ", align="^"):
        self.generate_fmt_strings(pad, align)
        self.pretty = pretty
        self.precision = max_precision - 1
        self.max_decimals_pretty = max_decimals_pretty

    def generate_fmt_strings(self, pad, align):
        base = "{:" + pad + align
        self._num_format = base + "{width}{type}}"
        self._str_format = base + "{width}}"

    def __call__(self, value, column_width):
        if isinstance(value, str):
            return self.format_str(value, column_width)
        else:
            return self.format_num(value, column_width)

    def format_num(self, value, column_width):
        digit_guess = int(log10(max(abs(value), 1))) + 1
        if value < 0:
            digit_guess += 1
        # Use scientific formatting
        if not (-5 < digit_guess < 6) or digit_guess > column_width:
            # Determine the number of decimals to use
            if self.pretty:
                decimals = min(max(column_width - 6, 1),
                               self.max_decimals_pretty)
            else:
                decimals = max(self.precision, 0)
            type_fmt = "." + str(decimals) + "e"
            return self._num_format.format(value,
                                           width=column_width,
                                           type=type_fmt)
        # Use regular formatting
        else:
            if isinstance(value, int):
                return self._num_format.format(value,
                                               width=column_width,
                                               type="d")
            else:
                # Determine the number of decimals to use
                if self.pretty:
                    decimals = min(max(column_width - digit_guess - 2, 1),
                                   self.max_decimals_pretty)
                else:
                    decimals = max(self.precision - digit_guess + 1, 0)
                type_fmt = "." + str(decimals) + "f"
                return self._num_format.format(value,
                                               width=column_width,
                                               type=type_fmt)

    def format_str(self, value, column_width):
        if self.pretty and len(value) > column_width:
            truncate_to = max(1, column_width - 2)
            return self._str_format.format(value[-truncate_to:],
                                           width=column_width)
        else:
            return self._str_format.format(value, width=column_width)


class _CSVInternal(_InternalCustomAction):
    """Implements the logic for a simple text based logger backend.

    This currently has to check the logged quantites every time to ensure it has
    not changed since the last run of `~.act`. Performance could be improved by
    allowing for writing of data without checking for a change in logged
    quantites, but would be more fragile.
    """

    def __init__(self, logger, output=stdout, header_sep='.', delimiter=' ',
                 pretty=True, max_precision=10, max_header_len=None):
        def writable(fh):
            if fh.mode[0] not in 'wxa' and fh.mode != 'r+':
                raise ValueError("File handle is not writable.")

        param_dict = ParameterDict(
            header_sep=str, delimiter=str, min_column_width=int,
            max_header_len=int, pretty=bool, max_precision=int,
            output=OnlyType(_OutputWriter, postprocess=writable))
        param_dict.update(dict(
            header_set=header_sep, delimiter=delimiter,
            min_column_width=max(10, max_precision + 6),
            max_header_len=max_header_len, max_precision=max_precision,
            pretty=pretty, output=output)
        )
        self._param_dict = param_dict

        # internal variables that are not part of the state.
        flags = set(logger.flags)
        if flags.difference(['scalar', 'string']) != set() \
                or 'scalar' not in flags:
            raise ValueError("Given Logger must have the scalar flag set.")

        self._logger = logger
        self._cur_headers_with_width = dict()
        self._fmt = _Formatter(pretty, max_precision)

    def _setattr_param(self, attr, value):
        """Makes self._param_dict attributes read only."""
        raise ValueError("Attribute {} is read-only.".format(attr))

    def attach(self, simulation):
        self._comm = simulation.device._comm

    def detach(self):
        self._comm = None

    def _get_log_dict(self):
        """Get a flattened dict for writing to output."""
        return {key: value[0]
                for key, value in dict_flatten(self._logger.log()).items()
                if value[1] in {'string', 'scalar'}
                }

    def _update_headers(self, new_keys):
        """Update headers and write the current headers to output.

        This function could be made simpler and faster by moving some of the
        transformation to act. Since we don't expect the headers to change often
        however, this would likely slow the writer down. The design is to
        off-load any potnentially unnecessary calculations to this function even
        if that means more overall computation when headers change.
        """
        header_output_list = []
        header_dict = {}
        for namespace in new_keys:
            header = self._determine_header(
                namespace, self._header_sep, self._max_header_len)
            column_size = max(len(header), self._min_width)
            header_dict[namespace] = column_size
            header_output_list.append((header, column_size))
        self._cur_headers_with_width = header_dict
        self._output.write(
            self._delimiter.join((self._fmt.format_str(hdr, width)
                                  for hdr, width in header_output_list))
        )
        self._output.write('\n')

    @staticmethod
    def _determine_header(namespace, sep, max_len):
        index = -1
        char_count = 0
        for name in reversed(namespace[:-1]):
            if char_count + len(name) > max_len:
                break
            index -= 1
        return sep.join(namespace[index:])

    def _write_row(self, data):
        """Write a row of data to output."""
        headers = self._cur_headers_with_width
        self._output.write(self._delimiter.join((self._fmt(data[k], headers[k])
                                                 for k in headers))
                           )
        self._output.write('\n')

    def act(self, timestep=None):
        """Write row to designated output.

        Will also write header when logged quantites are determined to have
        changed.
        """
        if self._comm is not None and self._comm.rank == 0:
            output_dict = self._get_log_dict()
            # determine if a header needs to be written. This is always the case
            # for the first call of act, and if the logged quantities change
            # within a run.
            new_keys = output_dict.keys()
            if new_keys != self._cur_headers_with_width.keys():
                self._update_headers(new_keys)

            # Write the data and flush. We must flush to ensure that the data
            # isn't merely stored in Python ready to be written later.
            self._write_row(output_dict)
            self._output.flush()


class CSV(_InternalCustomAnalyzer):
    """A delimiter separated value file backend for a Logger.

    This can serve as a way to output scalar simulation data to standard out.
    However, this is useable to store simulation scalar data to a file as well.

    Args:
        trigger (hoomd.trigger.Trigger): The trigger to determine when to run
            the CSV logger.
        logger (hoomd.logger.Logger): The logger to query for output. The
            'scalar' flag must be set on the logger, and the 'string' flag is
            optional.
        output (file handle, optional): A file-like object to output the data
            from, defaults to standard out. The object must have write and flush
            methods and a mode attribute.
        header_sep (string, optional): String to use to separate names in the
            logger's namespace, defaults to '.'. For example, if logging the
            total energy of an `hoomd.md.pair.LJ` pair force object, the default
            header would be ``md.pair.LJ.energy`` (assuming that
            ``max_header_len`` is not set).
        pretty (bool, optional): Flags whether to attempt to make output
            prettier and easier to read, defaults to True. To make the ouput
            easier to read, the output will compromise on outputted precision
            for improved readability. In many cases, though the precision will
            still be high with pretty set to ``True``.
        max_precision (int, optional): If pretty is not set, then this controls
            the maximum precision to use when outputing numerical values,
            defaults to 10.
        max_header_len (int, optional): If not None (the default), limit the
            outputted header names to length ``max_header_len``. When not None,
            names are grabbed from the most specific to the least. For example,
            if set to 7 the namespace 'hoomd.md.pair.LJ.energy' would be set to
            'energy'. Note that at least the most specific part of the namespace
            will be used regardless of this setting (e.g. if set to 5 in the
            previous example, we would still use 'energy' as the header).

    Note:
        This only works with scalar and string quantities.
    """
    _internal_class = _CSVInternal

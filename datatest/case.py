# -*- coding: utf-8 -*-
from __future__ import division

import inspect
import pprint
import re
from unittest import TestCase

from datatest.diff import DiffBase
from datatest.diff import ExtraColumn
from datatest.diff import ExtraValue
from datatest.diff import ExtraSum
from datatest.diff import MissingColumn
from datatest.diff import MissingValue
from datatest.diff import MissingSum

__datatest = True  # Used to detect in-module stack frames (which are
                   # omitted from output).

_re_type = type(re.compile(''))


class DataAssertionError(AssertionError):
    """Data assertion failed."""
    def __init__(self, msg, diff, trusted=None, subject=None):
        """Initialize self, store difference for later reference."""
        if not diff:
            raise ValueError('Missing difference.')
        self.diff = diff
        self.msg = msg
        self.trusted = str(trusted)  # Trusted data source or object.
        self.subject = str(subject)  # Subject data source.
        self._verbose = False  # <- Set by DataTestResult if verbose.

        return AssertionError.__init__(self, msg)

    def __repr__(self):
        return self.__class__.__name__ + ': ' + self.__str__()

    def __str__(self):
        diff = pprint.pformat(self.diff, width=1)
        if any([diff.startswith('{') and diff.endswith('}'),
                diff.startswith('[') and diff.endswith(']'),
                diff.startswith('(') and diff.endswith(')')]):
            diff = diff[1:-1]

        if self._verbose:
            msg_extras = '\n\nTRUSTED DATA:\n{0}\nSUBJECT DATA:\n{1}'
            msg_extras = msg_extras.format(self.trusted, self.subject)
        else:
            msg_extras = ''

        return '{0}:\n {1}{2}'.format(self.msg, diff, msg_extras)


def _walk_diff(diff):
    """Iterate over difference or collection of differences."""
    if isinstance(diff, dict):
        diff = diff.values()
    elif isinstance(diff, DiffBase):
        diff = (diff,)

    for item in diff:
        if isinstance(item, (dict, list, tuple)):
            for elt2 in _walk_diff(item):
                yield elt2
        else:
            if not isinstance(item, DiffBase):
                raise TypeError('Object {0!r} is not derived from DiffBase.'.format(item))
            yield item


class _BaseAcceptContext(object):
    def __init__(self, accepted, test_case, callable_obj=None):
        self.accepted = accepted
        self.test_case = test_case
        if callable_obj is not None:
            try:
                self.obj_name = callable_obj.__name__
            except AttributeError:
                self.obj_name = str(callable_obj)
        else:
            self.obj_name = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        raise NotImplementedError()

    def _raiseFailure(self, standardMsg, difference):
        msg = self.test_case._formatMessage(self.msg, standardMsg)
        subj = self.test_case.subjectData
        trst = self.test_case.trustedData
        try:
            # For Python 3.x (some 3.2 builds will raise a TypeError
            # while 2.x will raise SyntaxError).
            expr = 'raise DataAssertionError(msg, {0}, subj, trst) from None'
            exec(expr.format(repr(difference)))
        except (SyntaxError, TypeError):
            raise DataAssertionError(msg, difference, subj, trst)  # For Python 2.x

    def handle(self, name, callable_obj, args, kwds):
        """If callable_obj is None, assertRaises/Warns is being used as
        a context manager, so check for a 'msg' kwarg and return self.
        If callable_obj is not None, call it passing `args` and `kwds`.

        """
        if callable_obj is None:
            self.msg = kwds.pop('msg', None)
            return self
        with self:
            callable_obj(*args, **kwds)


class _AcceptDifferenceContext(_BaseAcceptContext):
    def __exit__(self, exc_type, exc_value, tb):
        difference = list(_walk_diff(exc_value.diff))
        accepted = list(_walk_diff(self.accepted))
        unaccepted = [x for x in difference if x not in accepted]
        if unaccepted:
            return self._raiseFailure(exc_value.msg, unaccepted)  # <- EXIT!

        accepted_not_found = [x for x in accepted if x not in difference]
        if accepted_not_found:
            message = exc_value.msg + ', accepted difference not found'
            return self._raiseFailure(message, accepted_not_found)  # <- EXIT!

        return True


class _AcceptAbsoluteToleranceContext(_BaseAcceptContext):
    def __init__(self, accepted, test_case, callable_obj=None):
        assert accepted >= 0, 'Tolerance cannot be defined with a negative number.'
        _BaseAcceptContext.__init__(self, accepted, test_case, callable_obj)

    def __exit__(self, exc_type, exc_value, tb):
        difference = list(_walk_diff(exc_value.diff))
        accepted = self.accepted

        failed = []
        for diff in difference:
            if abs(diff.diff) > accepted:
                failed.append(diff)
        if failed:
            return self._raiseFailure(exc_value.msg, failed)  # <- EXIT!

        return True


class _AcceptPercentToleranceContext(_BaseAcceptContext):
    def __init__(self, accepted, test_case, callable_obj=None):
        assert 1 >= accepted >= 0, 'Percent tolerance must be between 0 and 1.'
        _BaseAcceptContext.__init__(self, accepted, test_case, callable_obj)

    def __exit__(self, exc_type, exc_value, tb):
        difference = list(_walk_diff(exc_value.diff))
        accepted = self.accepted

        failed = []
        for diff in difference:
            percent = diff.diff / diff.sum
            if abs(percent) > accepted:
                failed.append(diff)
        if failed:
            return self._raiseFailure(exc_value.msg, failed)  # <- EXIT!

        return True


class DataTestCase(TestCase):
    """This class wraps unittest.TestCase to add a number of convinience
    methods for testing data quality.
    """
    def __getattr__(self, name):
        if name in ('trustedData', 'subjectData'):
            for record in inspect.stack():  # Bubble-up stack looking
                frame = record[0]           # for data source.
                if name in frame.f_globals:
                    return frame.f_globals[name]  # <- EXIT!
            raise NameError('name {0!r} is not defined'.format(name))
        else:
            obj = repr(self.__class__.__name__)
            attr = repr(name)
            message = '{0} object has no attribute {1}'.format(obj, attr)
            raise AttributeError(message)

    def assertDataColumnSet(self, trusted=None, msg=None):
        """Test that set of subject column names matches set of trusted
        column names.  If `trusted` is None, values are loaded from
        self.trustedData.
        """
        if trusted == None:
            trusted = set(self.trustedData.columns())
        subject = set(self.subjectData.columns())

        if subject != trusted:
            extra = [ExtraColumn(x) for x in subject - trusted]
            missing = [MissingColumn(x) for x in trusted - subject]
            if msg is None:
                msg = 'different column names'
            self.fail(msg, extra+missing)

    def assertDataColumnSubset(self, trusted=None, msg=None):
        """Test that set of subject column names is subset of trusted
        column names.  If `trusted` is None, values are loaded from
        self.trustedData.
        """
        if trusted == None:
            trusted = set(self.trustedData.columns())
        subject = set(self.subjectData.columns())

        if not subject.issubset(trusted):
            extra = subject.difference(trusted)
            extra = [ExtraColumn(x) for x in extra]
            if msg is None:
                msg = 'different column names'  # found extra columns
            self.fail(msg, extra)

    def assertDataColumnSuperset(self, trusted=None, msg=None):
        """Test that set of subject column names is superset of trusted
        column names.  If `trusted` is None, values are loaded from
        self.trustedData.
        """
        if trusted == None:
            trusted = set(self.trustedData.columns())
        subject = set(self.subjectData.columns())

        if not subject.issuperset(trusted):
            missing = trusted.difference(subject)
            missing = [MissingColumn(x) for x in missing]
            if msg is None:
                msg = 'different column names'  # missing expected columns
            self.fail(msg, missing)

    def assertDataSet(self, column, trusted=None, msg=None, **kwds):
        """Test that set in subject `column` matches set in trusted
        `column`.  If `trusted` is None, values are loaded from
        self.trustedData.
        """
        if trusted == None:
            trusted = self.trustedData.set(column, **kwds)
        subject = self.subjectData.set(column, **kwds)

        if subject != trusted:
            extra = [ExtraValue(x) for x in subject - trusted]
            missing = [MissingValue(x) for x in trusted - subject]
            if msg is None:
                msg = 'different {0!r} values'.format(column)
            self.fail(msg, extra+missing)

    def assertDataSubset(self, column, trusted=None, msg=None, **kwds):
        """Test that set in subject `column` is subset of trusted
        `column`.  If `trusted` is None, values are loaded from
        self.trustedData.
        """
        if trusted == None:
            trusted = self.trustedData.set(column, **kwds)
        subject = self.subjectData.set(column, **kwds)

        if not subject.issubset(trusted):
            extra = subject.difference(trusted)
            extra = [ExtraValue(x) for x in extra]
            if msg is None:
                msg = 'different {0!r} values'.format(column)
            self.fail(msg, extra)

    def assertDataSuperset(self, column, trusted=None, msg=None, **kwds):
        """Test that set in subject `column` is superset of trusted
        `column`.  If `trusted` is None, values are loaded from
        self.trustedData.
        """
        if trusted == None:
            trusted = self.trustedData.set(column, **kwds)
        subject = self.subjectData.set(column, **kwds)

        if not subject.issuperset(trusted):
            missing = trusted.difference(subject)
            missing = [MissingValue(x) for x in missing]
            if msg is None:
                msg = 'different {0!r} values'.format(column)
            self.fail(msg, missing)

    def assertDataSum(self, column, groupby, msg=None, **kwds):
        """Test that sum of subject `column` matches sum of trusted
        `column` grouped by given columns.
        """
        trusted = self.trustedData
        subject = self.subjectData

        def test(group_dict):
            all_kwds = kwds.copy()
            all_kwds.update(group_dict)
            subject_sum = subject.sum(column, **all_kwds)
            trusted_sum = trusted.sum(column, **all_kwds)
            s_sum = subject_sum if subject_sum else 0
            t_sum = trusted_sum if trusted_sum else 0
            difference = s_sum - t_sum
            if difference != 0:
                if difference > 0:
                    return ExtraSum(difference, t_sum, **group_dict)
                else:
                    return MissingSum(difference, t_sum, **group_dict)
            return None

        failures = [test(group_dict) for group_dict in trusted.groups(*groupby, **kwds)]
        failures = [x for x in failures if x != None]  # Filter for failures.
        if failures:
            if not msg:
                msg = 'different {0!r} sums'.format(column)
            self.fail(msg=msg, diff=failures)

    def assertDataRegex(self, column, regex, msg=None, **kwds):
        """Test that values in subject `column` match `regex` pattern
        search.
        """
        subject = self.subjectData.set(column, **kwds)
        if not isinstance(regex, _re_type):
            regex = re.compile(regex)
        failures = [x for x in subject if not regex.match(x)]
        failures = [ExtraValue(x) for x in failures]
        if failures:
            if not msg:
                msg = 'non-matching {0!r} values'.format(column)
            self.fail(msg=msg, diff=failures)

    def assertDataNotRegex(self, column, regex, msg=None, **kwds):
        """Test that values in subject `column` do not match `regex`
        pattern search.
        """
        subject = self.subjectData.set(column, **kwds)
        if not isinstance(regex, _re_type):
            regex = re.compile(regex)
        failures = [x for x in subject if regex.match(x)]
        failures = [ExtraValue(x) for x in failures]
        if failures:
            if not msg:
                msg = 'matching {0!r} values'.format(column)
            self.fail(msg=msg, diff=failures)

    def acceptDifference(self, diffs, callableObj=None, *args, **kwds):
        """Test that a DataAssertionError containing a matching
        collection of differences is raised when `callable` is called
        with `args` and keyword `kwds`. If the raised differences do not
        match the accepted differences, the test case will fail with a
        DataAssertionError of the remaining differences.

        If called with `callableObj` omitted or None, will return a
        context manager so that the code under test can be written
        inline rather than as a function::

            with self.acceptDifference(SomeDifferences):
                do_something()

        An optional keyword argument `msg` can be provided when
        acceptDifference is used as a context manager.
        """
        # TODO: Test the following behavior.
        #The context manager keeps a reference to the exception as
        #the `exception` attribute. This allows you to inspect the
        #exception after the assertion::
        #
        #    with self.acceptDifference(SomeDifferences) as cm:
        #        do_something()
        #    the_exception = cm.exception
        #    self.assertEqual(the_exception.error_code, 3)
        context = _AcceptDifferenceContext(diffs, self, callableObj)
        return context.handle('acceptDifference', callableObj, args, kwds)

    def acceptTolerance(self, tolerance, callableObj=None, *args, **kwds):
        """Only fail if DataAssertionError contains numeric differeces
        greater than the given tolerance.  If differences exceed the
        tolerance, the test case will fail with a DataAssertionError
        containing the excessive differences.

        Like acceptDifference, this method can be used as a context
        manager::

            with self.acceptTolerance(SomeTolerance):
                do_something()
        """
        context = _AcceptAbsoluteToleranceContext(tolerance, self, callableObj)
        return context.handle('acceptTolerance', callableObj, args, kwds)

    def acceptPercentTolerance(self, tolerance, callableObj=None, *args, **kwds):
        """Only fail if DataAssertionError contains numeric differece
        percentages greater than the given tolerance.  If differences
        exceed the tolerance, the test case will fail with a
        DataAssertionError containing the excessive differences.

        Like acceptDifference, this method can be used as a context
        manager::

            with self.acceptPercentTolerance(SomeTolerance):
                do_something()
        """
        context = _AcceptPercentToleranceContext(tolerance, self, callableObj)
        return context.handle('acceptPercentTolerance', callableObj, args, kwds)

    def fail(self, msg, diff=None):
        """Signals a test failure unconditionally, with `msg` for the
        error message.  If `diff` is provided, a DataAssertionError is
        raised instead of an AssertionError.
        """
        if diff:
            try:
                trusted = self.trustedData
            except NameError:
                trusted = None
            raise DataAssertionError(msg, diff, trusted, self.subjectData)
        else:
            raise self.failureException(msg)

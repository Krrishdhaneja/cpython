from cpython.__main__ import main, configure_logger

<<<<<<< HEAD
=======
from collections import namedtuple
import glob
import os.path
import re
import shutil
import sys
import subprocess


VERBOSITY = 2

C_GLOBALS_DIR = os.path.abspath(os.path.dirname(__file__))
TOOLS_DIR = os.path.dirname(C_GLOBALS_DIR)
ROOT_DIR = os.path.dirname(TOOLS_DIR)
GLOBALS_FILE = os.path.join(C_GLOBALS_DIR, 'ignored-globals.txt')

SOURCE_DIRS = ['Include', 'Objects', 'Modules', 'Parser', 'Python']

CAPI_REGEX = re.compile(r'^ *PyAPI_DATA\([^)]*\) \W*(_?Py\w+(?:, \w+)*\w).*;.*$')


IGNORED_VARS = {
        '_DYNAMIC',
        '_GLOBAL_OFFSET_TABLE_',
        '__JCR_LIST__',
        '__JCR_END__',
        '__TMC_END__',
        '__bss_start',
        '__data_start',
        '__dso_handle',
        '_edata',
        '_end',
        }


def find_capi_vars(root):
    capi_vars = {}
    for dirname in SOURCE_DIRS:
        for filename in glob.glob(os.path.join(
                                  glob.escape(os.path.join(ROOT_DIR, dirname)),
                                  '**/*.[hc]'),
                                  recursive=True):
            with open(filename) as file:
                for name in _find_capi_vars(file):
                    if name in capi_vars:
                        assert not filename.endswith('.c')
                        assert capi_vars[name].endswith('.c')
                    capi_vars[name] = filename
    return capi_vars


def _find_capi_vars(lines):
    for line in lines:
        if not line.startswith('PyAPI_DATA'):
            continue
        assert '{' not in line
        match = CAPI_REGEX.match(line)
        assert match
        names, = match.groups()
        for name in names.split(', '):
            yield name


def _read_global_names(filename):
    # These variables are shared between all interpreters in the process.
    with open(filename) as file:
        return {line.partition('#')[0].strip()
                for line in file
                if line.strip() and not line.startswith('#')}


def _is_global_var(name, globalnames):
    if _is_autogen_var(name):
        return True
    if _is_type_var(name):
        return True
    if _is_module(name):
        return True
    if _is_exception(name):
        return True
    if _is_compiler(name):
        return True
    return name in globalnames


def _is_autogen_var(name):
    return (
        name.startswith('PyId_') or
        '.' in name or
        # Objects/typeobject.c
        name.startswith('op_id.') or
        name.startswith('rop_id.') or
        # Python/graminit.c
        name.startswith('arcs_') or
        name.startswith('states_')
        )


def _is_type_var(name):
    if name.endswith(('Type', '_Type', '_type')):  # XXX Always a static type?
        return True
    if name.endswith('_desc'):  # for structseq types
        return True
    return (
        name.startswith('doc_') or
        name.endswith(('_doc', '__doc__', '_docstring')) or
        name.endswith('_methods') or
        name.endswith('_fields') or
        name.endswith(('_memberlist', '_members')) or
        name.endswith('_slots') or
        name.endswith(('_getset', '_getsets', '_getsetlist')) or
        name.endswith('_as_mapping') or
        name.endswith('_as_number') or
        name.endswith('_as_sequence') or
        name.endswith('_as_buffer') or
        name.endswith('_as_async')
        )


def _is_module(name):
    if name.endswith(('_functions', 'Methods', '_Methods')):
        return True
    if name == 'module_def':
        return True
    if name == 'initialized':
        return True
    return name.endswith(('module', '_Module'))


def _is_exception(name):
    # Other vars are enumerated in globals-core.txt.
    if not name.startswith(('PyExc_', '_PyExc_')):
        return False
    return name.endswith(('Error', 'Warning'))


def _is_compiler(name):
    return (
        # Python/Python-ast.c
        name.endswith('_type') or
        name.endswith('_singleton') or
        name.endswith('_attributes')
        )


class Var(namedtuple('Var', 'name kind scope capi filename')):

    @classmethod
    def parse_nm(cls, line, expected, ignored, capi_vars, globalnames):
        _, _, line = line.partition(' ')  # strip off the address
        line = line.strip()
        kind, _, line = line.partition(' ')
        if kind in ignored or ():
            return None
        elif kind not in expected or ():
            raise RuntimeError('unsupported NM type {!r}'.format(kind))

        name, _, filename = line.partition('\t')
        name = name.strip()
        if _is_autogen_var(name):
            return None
        if _is_global_var(name, globalnames):
            scope = 'global'
        else:
            scope = None
        capi = (name in capi_vars or ())
        if filename:
            filename = os.path.relpath(filename.partition(':')[0])
        return cls(name, kind, scope, capi, filename or '~???~')

    @property
    def external(self):
        return self.kind.isupper()


def find_vars(root, globals_filename=GLOBALS_FILE):
    python = os.path.join(root, 'python')
    if not os.path.exists(python):
        raise RuntimeError('python binary missing (need to build it first?)')
    capi_vars = find_capi_vars(root)
    globalnames = _read_global_names(globals_filename)

    nm = shutil.which('nm')
    if nm is None:
        # XXX Use dumpbin.exe /SYMBOLS on Windows.
        raise NotImplementedError
    else:
        yield from (var
                    for var in _find_var_symbols(python, nm, capi_vars,
                                                 globalnames)
                    if var.name not in IGNORED_VARS)


NM_FUNCS = set('Tt')
NM_PUBLIC_VARS = set('BD')
NM_PRIVATE_VARS = set('bd')
NM_VARS = NM_PUBLIC_VARS | NM_PRIVATE_VARS
NM_DATA = set('Rr')
NM_OTHER = set('ACGgiINpSsuUVvWw-?')
NM_IGNORED = NM_FUNCS | NM_DATA | NM_OTHER


def _find_var_symbols(python, nm, capi_vars, globalnames):
    args = [nm,
            '--line-numbers',
            python]
    out = subprocess.check_output(args)
    for line in out.decode('utf-8').splitlines():
        var = Var.parse_nm(line, NM_VARS, NM_IGNORED, capi_vars, globalnames)
        if var is None:
            continue
        yield var


#######################################

class Filter(namedtuple('Filter', 'name op value action')):

    @classmethod
    def parse(cls, raw):
        action = '+'
        if raw.startswith(('+', '-')):
            action = raw[0]
            raw = raw[1:]
        # XXX Support < and >?
        name, op, value = raw.partition('=')
        return cls(name, op, value, action)

    def check(self, var):
        value = getattr(var, self.name, None)
        if not self.op:
            matched = bool(value)
        elif self.op == '=':
            matched = (value == self.value)
        else:
            raise NotImplementedError

        if self.action == '+':
            return matched
        elif self.action == '-':
            return not matched
        else:
            raise NotImplementedError


def filter_var(var, filters):
    for filter in filters:
        if not filter.check(var):
            return False
    return True


def make_sort_key(spec):
    columns = [(col.strip('_'), '_' if col.startswith('_') else '')
               for col in spec]
    def sort_key(var):
        return tuple(getattr(var, col).lstrip(prefix)
                     for col, prefix in columns)
    return sort_key


def make_groups(allvars, spec):
    group = spec
    groups = {}
    for var in allvars:
        value = getattr(var, group)
        key = '{}: {}'.format(group, value)
        try:
            groupvars = groups[key]
        except KeyError:
            groupvars = groups[key] = []
        groupvars.append(var)
    return groups


def format_groups(groups, columns, fmts, widths):
    for group in sorted(groups):
        groupvars = groups[group]
        yield '', 0
        yield '  # {}'.format(group), 0
        yield from format_vars(groupvars, columns, fmts, widths)


def format_vars(allvars, columns, fmts, widths):
    fmt = ' '.join(fmts[col] for col in columns)
    fmt = ' ' + fmt.replace(' ', '   ') + ' '  # for div margin
    header = fmt.replace(':', ':^').format(*(col.upper() for col in columns))
    yield header, 0
    div = ' '.join('-'*(widths[col]+2) for col in columns)
    yield div, 0
    for var in allvars:
        values = (getattr(var, col) for col in columns)
        row = fmt.format(*('X' if val is True else val or ''
                           for val in values))
        yield row, 1
    yield div, 0


#######################################

COLUMNS = 'name,external,capi,scope,filename'
COLUMN_NAMES = COLUMNS.split(',')

COLUMN_WIDTHS = {col: len(col)
                 for col in COLUMN_NAMES}
COLUMN_WIDTHS.update({
        'name': 50,
        'scope': 7,
        'filename': 40,
        })
COLUMN_FORMATS = {col: '{:%s}' % width
                  for col, width in COLUMN_WIDTHS.items()}
for col in COLUMN_FORMATS:
    if COLUMN_WIDTHS[col] == len(col):
        COLUMN_FORMATS[col] = COLUMN_FORMATS[col].replace(':', ':^')


def _parse_filters_arg(raw, error):
    filters = []
    for value in raw.split(','):
        value=value.strip()
        if not value:
            continue
        try:
            filter = Filter.parse(value)
            if filter.name not in COLUMN_NAMES:
                raise Exception('unsupported column {!r}'.format(filter.name))
        except Exception as e:
            error('bad filter {!r}: {}'.format(raw, e))
        filters.append(filter)
    return filters


def _parse_columns_arg(raw, error):
    columns = raw.split(',')
    for column in columns:
        if column not in COLUMN_NAMES:
            error('unsupported column {!r}'.format(column))
    return columns


def _parse_sort_arg(raw, error):
    sort = raw.split(',')
    for column in sort:
        if column.lstrip('_') not in COLUMN_NAMES:
            error('unsupported column {!r}'.format(column))
    return sort


def _parse_group_arg(raw, error):
    if not raw:
        return raw
    group = raw
    if group not in COLUMN_NAMES:
        error('unsupported column {!r}'.format(group))
    if group != 'filename':
        error('unsupported group {!r}'.format(group))
    return group


def parse_args(argv=None):
    if argv is None:
        argv = sys.argv[1:]
>>>>>>> 3.9

def parse_args():
    import argparse
    from c_common.scriptutil import (
        add_verbosity_cli,
        add_traceback_cli,
        process_args_by_key,
    )
    from cpython.__main__ import _cli_check
    parser = argparse.ArgumentParser()
    processors = [
        add_verbosity_cli(parser),
        add_traceback_cli(parser),
        _cli_check(parser, checks='<globals>'),
    ]

    args = parser.parse_args()
    ns = vars(args)

    cmd = 'check'
    verbosity, traceback_cm = process_args_by_key(
        args,
        processors,
        ['verbosity', 'traceback_cm'],
    )

    return cmd, ns, verbosity, traceback_cm


(cmd, cmd_kwargs, verbosity, traceback_cm) = parse_args()
configure_logger(verbosity)
with traceback_cm:
    main(cmd, cmd_kwargs)

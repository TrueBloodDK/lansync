"""Translate a strict TOML configuration into ordinary CLI arguments."""
from pathlib import Path
import math
import tomllib


def load_arguments(path, commands):
    path = Path(path).expanduser().resolve(strict=True)
    with path.open('rb') as stream:
        config = tomllib.load(stream)
    command = config.pop('command', None)
    if not isinstance(command, str) or command not in {'scan', 'watch', 'serve', 'pull', 'sync'}:
        raise ValueError('Config command must be scan, watch, serve, pull or sync')
    parser = commands.choices[command]
    actions = {a.dest: a for a in parser._actions if a.dest != 'help'}
    unknown = config.keys() - actions.keys()
    if unknown:
        raise ValueError('Unknown config keys: ' + ', '.join(sorted(unknown)))
    positional, options = [], []
    for key, action in actions.items():
        if key not in config:
            if action.required:
                raise ValueError(f'Missing config key: {key}')
            continue
        value = config[key]
        if action.nargs == 0:
            if type(value) is not bool:
                raise ValueError(f'{key} must be a boolean')
            if value:
                options.append(action.option_strings[0])
            continue
        values = value if key == 'exclude' else [value]
        if not isinstance(values, list):
            raise ValueError('exclude must be an array of strings')
        for item in values:
            if action.type in (int, float):
                if type(item) not in (int, float) or (type(item) is float and not math.isfinite(item)):
                    raise ValueError(f'{key} must be a finite number')
                if action.type is int and type(item) is not int:
                    raise ValueError(f'{key} must be an integer')
            elif not isinstance(item, str) or not item:
                raise ValueError(f'{key} must be a nonempty string')
            if action.type is Path:
                item = Path(item).expanduser()
                if not item.is_absolute():
                    item = path.parent / item
                item = str(item.resolve())
            if action.option_strings:
                options.append(action.option_strings[0] + '=' + str(item))
            else:
                positional.append(str(item))
    return [command, *options, '--', *positional]

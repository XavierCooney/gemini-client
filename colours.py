from typing import Union, List, Optional, Iterable, Dict
import configparser

StrOrColouredString = Union[str, 'ColouredString']

class Colour:
    def __init__(self, sgrs: List[str]):
        self._sgrs = sgrs

    def as_ansi_escape(self) -> str:
        return '\x1b[m' + ''.join('\x1b[' + sgr + 'm' for sgr in self._sgrs)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Colour):
            return self._sgrs == other._sgrs
        return False

no_colour = Colour([])

class ColouredStringIter:
    def __init__(self, cs: 'ColouredString'):
        self.cs = cs
        self.i = 0
        self.colour_index = 0

    def __next__(self) -> 'ColouredString':
        if self.i >= len(self.cs._s):
            raise StopIteration
        self.i += 1
        return self.cs[self.i - 1]

class ColouredString:
    def __init__(self, s: str, ctx: 'ColourContext', colours: Optional[List[Colour]]=None):
        self._s = s
        self._ctx = ctx

        if colours is None:
            self._colours: List[Colour] = [no_colour] * len(self._s)
        else:
            assert len(colours) == len(self._s)
            self._colours = colours

    def convert_to_coloured_string(self, other: object) -> 'ColouredString':
        if isinstance(other, ColouredString):
            assert self._ctx is other._ctx
            return other
        elif isinstance(other, str):
            return ColouredString(other, self._ctx)
        else:
            raise NotImplemented

    def __str__(self) -> str:
        if self._ctx.do_colours:
            ret = []
            prev_col = no_colour

            for c, col in zip(self._s, self._colours):
                if col != prev_col:
                    ret.append(col.as_ansi_escape())
                    prev_col = col
                ret.append(c)

            if prev_col != no_colour:
                ret.append(no_colour.as_ansi_escape())
                ret.append('\x1b[m')

            return ''.join(ret)
        else:
            return self._s

    def raw(self) -> str:
        return self._s

    def apply_colour(self, col: Colour) -> 'ColouredString':
        return ColouredString(
            self._s, self._ctx, [col] * len(self._s)
        )

    def __add__(self, other: StrOrColouredString) -> 'ColouredString':
        converted = self.convert_to_coloured_string(other)
        cs = ColouredString(
            self._s + converted._s, self._ctx,
            self._colours + converted._colours
        )
        return cs

    def __radd__(self, other: StrOrColouredString) -> 'ColouredString':
        converted = self.convert_to_coloured_string(other)
        cs = ColouredString(
            converted._s + self._s, self._ctx,
            converted._colours + self._colours
        )
        return cs

    def __eq__(self, other: object) -> bool:
        converted = self.convert_to_coloured_string(other)
        return self._s == converted._s # and self._colours == converted._colours

    def __iter__(self) -> ColouredStringIter:
        return ColouredStringIter(self)

    def __getitem__(self, idx_or_slice: Union[int, slice]) -> 'ColouredString':
        if isinstance(idx_or_slice, int):
            return ColouredString(
                self._s[idx_or_slice], self._ctx, [self._colours[idx_or_slice]]
            )
        else:
            return ColouredString(
                self._s[idx_or_slice], self._ctx, self._colours[idx_or_slice]
            )

    def join(self, collection: Iterable['ColouredString']) -> 'ColouredString':
        raw_strings = []
        colour_data = []

        first = True

        for cs in collection:
            if not first:
                colour_data.extend(self._colours)
                raw_strings.append(self._s)

            colour_data.extend(cs._colours)
            raw_strings.append(cs._s)
            assert cs._ctx is self._ctx

            first = False

        cs = ColouredString(
            ''.join(raw_strings), self._ctx, colour_data
        )

        return cs

    def __len__(self) -> int:
        return len(self._s)

class ThemeError(Exception):
    def __init__(self, msg: str):
        super().__init__()
        self.msg = msg

class ColourContext:
    def __init__(self, do_colours: bool, theme_file: str):
        self.config = configparser.ConfigParser()
        self.config.read(theme_file)
        self.do_colours = do_colours
        self.colour_cache: Dict[str, Colour] = {}
        self.make_ansi_escape_table()

    def make_ansi_escape_table(self) -> None:
        colours = {
            'black': 30,
            'red': 31,
            'green': 32,
            'yellow': 33,
            'blue': 34,
            'magenta': 35,
            'cyan': 36,
            'white': 37,
            'bright-black': 90,
            'bright-red': 91,
            'bright-green': 92,
            'bright-yellow': 93,
            'bright-blue': 94,
            'bright-magenta': 95,
            'bright-cyan': 96,
            'bright-white': 97,
        }
        foregrounds = {
            (k + '-foreground'): v for k, v in colours.items()
        }
        backgrounds = {
            (k + '-background'): v + 10 for k, v in colours.items()
        }
        self.ansi_escape_table = {
            'bold': 1,
            'italic': 3,
            'underline': 4,
        }
        self.ansi_escape_table.update(foregrounds)
        self.ansi_escape_table.update(backgrounds)

    def get_colour(self, name: str, backup: Optional[str]=None) -> Colour:
        if name in self.colour_cache:
            return self.colour_cache[name]

        selected_theme = self.config['DEFAULT']

        if name in selected_theme:
            value = selected_theme[name]
        elif backup is not None and backup in selected_theme:
            value = selected_theme[backup]
        else:
            raise ThemeError('Theme file is missing key ' + name)

        sgrs = []

        for requirement in value.split(' '):
            if requirement in self.ansi_escape_table:
                sgrs.append(str(self.ansi_escape_table[requirement]))
            elif requirement == 'default':
                continue
            else:
                raise ThemeError('unknown theme requirement `' + requirement + '` in ' + name)

        colour = Colour(sgrs)
        self.colour_cache[name] = colour
        return colour
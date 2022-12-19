from typing import Union, List, Tuple, Optional, Iterable

StrOrColouredString = Union[str, 'ColouredString']

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
    def __init__(self, s: str, ctx: 'ColourContext'):
        self._s = s
        self._colours: List[Tuple[int, str]] = []
        self._ctx = ctx

    def convert_to_coloured_string(self, other: object) -> 'ColouredString':
        if isinstance(other, ColouredString):
            assert self._ctx is other._ctx
            return other
        elif isinstance(other, str):
            return ColouredString(other, self._ctx)
        else:
            raise NotImplemented

    def offset_colours(self, n: int) -> List[Tuple[int, str]]:
        return [(x + n, c) for x, c in self._colours]

    def __str__(self) -> str:
        if self._ctx.do_colours:
            ret = []
            colour_idx = 0
            for i, c in enumerate(self._s):
                while colour_idx < len(self._colours) and self._colours[colour_idx][0] == i:
                    ret.append(self._colours[colour_idx][1])
                    colour_idx += 1
                ret.append(c)
            return ''.join(ret)
        else:
            return self._s

    def raw(self) -> str:
        return self._s

    def apply_sgr(self, sgr: str) -> 'ColouredString':
        assert self._colours == []
        cs = ColouredString(self._s, self._ctx)
        cs._colours.append((0, '\x1b[' + sgr + 'm'))
        cs._colours.append((len(self), '\x1b[m'))
        return cs

    def __add__(self, other: StrOrColouredString) -> 'ColouredString':
        converted = self.convert_to_coloured_string(other)
        cs = ColouredString(
            self._s + converted._s, self._ctx
        )
        if converted._colours:
            cs._colours = self._colours + converted.offset_colours(len(self._s))
        else:
            cs._colours = self._colours
        return cs

    def __radd__(self, other: StrOrColouredString) -> 'ColouredString':
        converted = self.convert_to_coloured_string(other)
        cs = ColouredString(
            converted._s + self._s, self._ctx
        )
        if self._colours:
            cs._colours = converted._colours + self.offset_colours(len(self._s))
        else:
            cs._colours = converted._colours
        return cs

    def __eq__(self, other: object) -> bool:
        converted = self.convert_to_coloured_string(other)
        return self._s == converted._s

    def __iter__(self) -> ColouredStringIter:
        return ColouredStringIter(self)

    def split_at(self, idx: int) -> Tuple['ColouredString', 'ColouredString']:
        # as in Haskell's Data.List.splitAt
        if idx <= 0:
            return ColouredString('', self._ctx), self
        elif idx >= len(self):
            return self, ColouredString('', self._ctx)

        before_s, after_s = self._s[:idx], self._s[idx:]
        before_colours, after_colours = [], []
        for i, colour in self._colours:
            if i < idx:
                before_colours.append((i, colour))
            else:
                after_colours.append((i - idx, colour))
        before = ColouredString(before_s, self._ctx)
        before._colours = before_colours
        after = ColouredString(after_s, self._ctx)
        after._colours = after_colours

        return before, after

    def __getitem__(self, idx: int) -> 'ColouredString':
        if idx > len(self._s) or idx < 0:
            raise IndexError

        c = self._s[idx]
        colour: Optional[str] = None
        for colour_idx, this_colour in self._colours:
            if colour_idx > idx: break
            else: colour = this_colour

        cs = ColouredString(c, self._ctx)
        if colour is not None:
            cs._colours = [(0, colour)]

        return cs

    def join(self, collection: Iterable['ColouredString']) -> 'ColouredString':
        raw_strings = []
        total_length = 0
        colour_data = []

        first = True

        for cs in collection:
            if not first:
                colour_data.extend(self.offset_colours(total_length))
                raw_strings.append(self._s)
                total_length += len(self._s)

            colour_data.extend(cs.offset_colours(total_length))
            raw_strings.append(cs._s)
            assert cs._ctx is self._ctx
            total_length += len(cs._s)

            first = False

        cs = ColouredString(''.join(raw_strings), self._ctx)
        cs._colours = colour_data

        return cs

    def __len__(self) -> int:
        return len(self._s)

class ColourContext:
    def __init__(self, do_colours: bool):
        self.do_colours = do_colours

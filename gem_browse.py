#!/usr/bin/env python3

from typing import List, Optional, Tuple, Dict, Callable
import shutil
import sys
import urllib.parse
import re

from gem_client import fetch_gem, GeminiError, GeminiResponse, TrustPolicy
from colours import ColouredString, ColourContext, ThemeError

PAGE_COL_SUBTRACT = 5
PAGE_LINES_SUBTRACT = 2

class Page:
    def __init__(self, url: str, response: GeminiResponse, browser: 'Browser', toc_of: Optional['Page']):
        self.url = url
        self.response = response
        self.browser = browser
        _, self.body = response.decoded_body_or_err()

        self.toc_of = toc_of

        if toc_of is None:
            seperator = '\n' if '\r\n' not in self.body else '\r\n'
            sanitised_body = ''.join(
                c if ord(c) >= 32 or c in '\r\n' else (f'\\x{ord(c):02x}' if c != '\t' else '    ')
                for c in self.body
            )
            self.input_lines = sanitised_body.split(seperator)
        else:
            self.input_lines = self.find_toc_lines()[0]

        self.prev_render_term_size: Optional[Tuple[int, int]] = None
        self.status = ''
        self.input_to_output_lines: Optional[List[int]] = None
        self.links: List[str] = []
        self.scroll_pos: int = 0

        self.render()

    def find_toc_lines(self) -> Tuple[List[str], List[int]]:
        assert self.toc_of

        toc_lines: List[str] = ['# Table of Contents', '']
        header_positions: List[int] = []
        for i, line in enumerate(self.toc_of.input_lines):
            if line.startswith('#'):
                num = f'[{len(header_positions) + 1}]'
                toc_lines.append(f'{num: >5} {line}')
                header_positions.append(i)

        return toc_lines, header_positions

    def handle_toc_selection(self, num: int) -> None:
        assert self.toc_of
        assert self.toc_of.input_to_output_lines

        positions = self.find_toc_lines()[1]
        num -= 1
        if num < 0 or num >= len(positions):
            self.browser.error_alert(['Invalid table of contents selection'])
            return

        pos = positions[num]
        self.toc_of.scroll_pos = self.toc_of.input_to_output_lines[pos]
        self.browser.page = self.toc_of

    def render(self) -> bool:
        term_cols, term_lines = shutil.get_terminal_size()

        if self.prev_render_term_size is None:
            self.status = 'INITIAL'
        elif self.prev_render_term_size != (term_cols, term_lines):
            self.status = 'RESIZE'
        else:
            return False

        self.name = None
        self.prev_render_term_size = (term_cols, term_lines)

        self.links = []

        term_cols -= PAGE_COL_SUBTRACT
        term_cols = max(term_cols, 5)

        theme = self.browser.theme
        empty_str = ColouredString('', theme)

        output_lines = [empty_str]
        input_to_output_lines = []

        lines_to_render = self.input_lines

        for input_line_num, input_line in enumerate(lines_to_render):
            input_to_output_lines.append(len(output_lines) - 1)

            page_text_colour = theme.get_colour('page_text')
            link_syntax_colour = theme.get_colour('link_syntax')
            link_number_colour = theme.get_colour('link_number')

            if m := re.match(r'^=>[ \t]*([^\t ]+)[ \t]*(.*)$', input_line):
                url, text = m.group(1), m.group(2)
                if not text: text = url
                self.links.append(url)


                processed_line = ColouredString('', theme).join([
                    ColouredString('=> [', theme).apply_colour(link_syntax_colour),
                    ColouredString(str(len(self.links)), theme).apply_colour(link_number_colour),
                    ColouredString(']', theme).apply_colour(link_syntax_colour),
                    ColouredString(f': {text}', theme).apply_colour(page_text_colour),
                ])
            elif self.toc_of and (m := re.match(r'^( *)\[(\d+)\] (#+)(.*)$', input_line)):
                whitespace, link_num, hashes, text = m.groups()

                header_colour = theme.get_colour('h' + str(len(hashes)), 'header')

                processed_line = ColouredString('', theme).join([
                    ColouredString(whitespace, theme),
                    ColouredString('[', theme).apply_colour(link_syntax_colour),
                    ColouredString(link_num, theme).apply_colour(link_number_colour),
                    ColouredString(']', theme).apply_colour(link_syntax_colour),
                    ColouredString(' ', theme),
                    ColouredString(f'{hashes}{text}', theme).apply_colour(header_colour),
                ])
            elif m := re.match(r'^(#+)(.*)$', input_line):
                level = len(m.group(1))
                if level == 1 and self.name is None:
                    self.name = m.group(2)
                colour = theme.get_colour('h' + str(level), 'header')
                processed_line = ColouredString(input_line, theme).apply_colour(colour)
            else:
                processed_line = ColouredString(input_line, theme).apply_colour(page_text_colour)


            words: List[Tuple[ColouredString, ColouredString]] = []
            this_whitespace_prefix: List[ColouredString] = []
            this_word: List[ColouredString] = []
            for c in processed_line:
                if c.raw() in ' \t':
                    if this_word:
                        words.append((
                            empty_str.join(this_whitespace_prefix),
                            empty_str.join(this_word),
                        ))
                        this_whitespace_prefix = []
                        this_word = []
                    this_whitespace_prefix.append(c)
                else:
                    this_word.append(c)
            words.append((
                empty_str.join(this_whitespace_prefix),
                empty_str.join(this_word),
            ))

            for whitespace, word in words:
                if_append_same_line = output_lines[-1] + whitespace + word

                if len(if_append_same_line) <= term_cols:
                    output_lines[-1] = if_append_same_line
                    continue

                while word:
                    this_line, word = word[:term_cols - 1], word[term_cols - 1:]

                    if word:
                        this_line += ColouredString('\\', theme).apply_colour(
                            theme.get_colour('line_continuation')
                        )

                    if output_lines[-1] == '':
                        output_lines[-1] = this_line
                    else:
                        output_lines.append(this_line)
            output_lines.append(empty_str)

        while not output_lines[-1]:
            output_lines.pop()

        self.lines = output_lines
        if self.input_to_output_lines is not None:
            old_input_line_idx = 0
            for i, x in enumerate(self.input_to_output_lines):
                if x > self.scroll_pos: break
                old_input_line_idx = i
            self.scroll_pos = input_to_output_lines[old_input_line_idx]
        else:
            self.scroll_pos = 0
        self.input_to_output_lines = input_to_output_lines

        return True

    def toggle_toc(self) -> 'Page':
        if self.toc_of:
            return self.toc_of
        elif self.url != 'internal://history':
            return Page(self.url, self.response, self.browser, self)
        else:
            self.browser.error_alert(['This page doesn\'t really need a table of contents'])
            return self

    def display(self, do_render: bool=True) -> None:
        if do_render:
            self.render()

        term_cols, term_lines = shutil.get_terminal_size()

        term_lines -= PAGE_LINES_SUBTRACT
        term_lines -= len(self.browser.alert_lines)

        joiner = self.browser.joiner

        lines = self.lines[self.scroll_pos:self.scroll_pos+term_lines]
        lines = [
            ('  │ ' if self.browser.use_unicode else '  | ') + line
            for line in lines
        ]
        lines += [ColouredString('  ^', self.browser.theme)] * (term_lines - len(lines))

        status_from_toc = ' [TOC]' if self.toc_of else ''
        status_line = f'[{self.status}]{status_from_toc} #{self.scroll_pos + 1}/{len(self.lines)}'

        content = str(ColouredString(joiner, self.browser.theme).join(lines))

        print(status_line, end=joiner)
        print(content, end=joiner)

    def scroll_down_1(self) -> None:
        self.scroll_pos += 1
        self.clamp()

    def scroll_up_1(self) -> None:
        self.scroll_pos -= 1
        self.clamp()

    def half(self) -> int:
        term_cols, term_lines = shutil.get_terminal_size()
        return max(2, term_lines // 2)

    def scroll_up_half(self) -> None:
        self.scroll_pos -= self.half()
        self.clamp()

    def scroll_down_half(self) -> None:
        self.scroll_pos += self.half()
        self.clamp()

    def clamp(self) -> None:
        self.scroll_pos = max(self.scroll_pos, 0)
        self.scroll_pos = min(self.scroll_pos, len(self.lines) - 1)

class Browser:
    def __init__(self, use_unicode: bool, use_colour: bool) -> None:
        self.history: List[Tuple[str, str]] = []
        self.page: Optional[Page] = None
        self.done = False
        self.has_term_control = False
        self.buffer: List[str] = []
        self.alert_lines: List[str] = []
        self.yes_no_prompt: Optional[Tuple[List[str], Callable[[bool], None]]] = None
        self.more_input_required: Optional[Tuple[str, str, bool]] = None
        self.joiner = '\n'
        self.page_cache: Dict[str, Page] = {}

        self.use_unicode = use_unicode
        self.use_colour = use_colour
        self.theme = ColourContext(self.use_colour, "theme.ini")

    def process_comand(self, command: str) -> None:
        if self.more_input_required:
            if command == '':
                self.more_input_required = None
                return self.error_alert(['Input dismissed'])
            else:
                old_url = urllib.parse.urlparse(self.more_input_required[0])
                new_url = urllib.parse.urlunparse([
                    old_url.scheme, old_url.netloc, old_url.path,
                    '', urllib.parse.quote(command, safe=''), ''
                ])
                self.more_input_required = None
                self.buffer = []
                self.go(new_url, True)
                return

        if self.yes_no_prompt:
            if command and command.lower() in 'yn':
                func = self.yes_no_prompt[1]
                self.yes_no_prompt = None
                func(command.lower() == 'y')
                self.display()
            return

        cmd, *args = command.split(' ')
        if command == '' and self.page:
            if self.page.render() or self.alert_lines:
                self.alert_lines = []
                return
            else:
                self.page.scroll_down_1()
        elif command == 'u' and self.page:
            self.page.scroll_up_half()
        elif command == 'd' and self.page:
            self.page.scroll_down_half()
        elif command == 'q':
            self.error_alert(['Bye!'])
            self.done = True
        elif command and 'history'.startswith(command):
            self.show_history()
        elif command == '?':
            self.go('internal://help')
        elif command in ('t', 'toc', 'table') and self.page:
            self.page = self.page.toggle_toc()
        elif command == 'back' or command == 'b':
            if len(self.history) < 2:
                self.error_alert(['Can\'t go back!'])
            else:
                self.history.pop()
                self.go(self.history[-1][0])
        elif cmd == 'go' or cmd == 'g':
            if len(args) != 1:
                return self.error_alert(['The go command requires one arg'])
            url = self.resolve_link(args[0], True)
            if url is None: return
            self.go(url)
        elif cmd == 'i':
            if len(args) != 1:
                return self.error_alert(['The go command requires one arg'])
            url = self.resolve_link(args[0], True)
            if url is None: return
            self.error_alert(['URL:', url])
        elif re.match(r'^[0-9]+$', command):
            if self.page and self.page.toc_of:
                return self.page.handle_toc_selection(int(command))

            url = self.resolve_link(command, True)
            if url is None: return
            self.go(url)
        elif command in ('reload', 'refresh') and self.page:
            if self.page.url in self.page_cache:
                del self.page_cache[self.page.url]
            self.go(self.page.url)
        else:
            return self.error_alert(['Unknown command!'])

    def show_history(self) -> None:
        if self.page and self.page.url == 'internal://history':
            self.go(self.history[-1][0])
        else:
            self.go('internal://history')

    def resolve_relative(self, old_url: str, path: str, allow_relative: bool) -> Optional[str]:
        current_url = urllib.parse.urlparse(old_url)
        if not current_url.netloc:
            self.error_alert(['Can\'t process link: invalid current page'])
            return None

        if path.startswith('/'):
            return urllib.parse.urlunparse([
                current_url.scheme, current_url.netloc,
                path, '', '', ''
            ])

        if not allow_relative:
            self.error_alert(['Invalid link!'])
            return None

        prev_compontents = list(filter(None, current_url.path.split('/')[:-1]))

        for c in path.split('/'):
            if c == '.': continue
            elif c == '..': prev_compontents = prev_compontents[:-1]
            elif not c: continue
            else:
                prev_compontents.append(c)

        return urllib.parse.urlunparse([
            current_url.scheme, current_url.netloc,
            '/' + '/'.join(prev_compontents) + ('/' if path[-1:] == '/' else ''),
            '', '', ''
        ])

    def looks_like_url(self, link: str) -> bool:
        return bool(re.match(r'^[a-zA-Z+.-]*:?//', link))

    def resolve_link(self, link: str, from_user: bool=False) -> Optional[str]:
        if self.looks_like_url(link): # looks like a url
            return link

        if not self.page:
            self.error_alert(['Can\'t resolve link, not currently on a page'])
            return None

        try:
            as_int = int(link) - 1
        except ValueError:
            pass
        else:
            links = self.page.links
            if 0 <= as_int < len(links) and from_user:
                return self.resolve_link(links[as_int], False)
            else:
                self.error_alert(['Invalid numeric link!'])
                return None

        if link == '.':
            return self.page.url

        return self.resolve_relative(self.page.url, link, not from_user)

    def read_raw_char(self) -> None:
        ch = sys.stdin.read(1)
        normal_prompt = self.more_input_required is None and self.yes_no_prompt is None
        quick_comands = ['u', 'd', 'h', 't']
        if ch in quick_comands and len(self.buffer) == 0 and normal_prompt:
            self.process_comand(ch)
            self.display()
        elif ch.lower() in 'yn' and self.yes_no_prompt:
            func = self.yes_no_prompt[1]
            self.yes_no_prompt = None
            func(ch.lower() == 'y')
            self.display()
        elif ch == '\r' and not self.yes_no_prompt:
            self.process_comand(''.join(self.buffer))
            self.buffer = []
            self.display()
        elif ch == '\x7f' and not self.yes_no_prompt:
            self.buffer = self.buffer[:-1]
            self.display()
        elif ch == 'e' and self.page and len(self.buffer) == 0 and normal_prompt:
            self.buffer = list('go ' + self.page.url)
            self.display()
        elif not self.yes_no_prompt:
            self.buffer.append(ch)
            if len(self.buffer) >= 3 and self.buffer[-3:][:2] == ['\x1b', '[']:
                c = self.buffer[-1]
                if c == 'A' and self.page and normal_prompt:
                    self.page.scroll_up_1()
                elif c == 'B' and self.page and normal_prompt:
                    self.page.scroll_down_1()
                self.buffer = self.buffer[:-3]
                self.display()
            elif self.buffer[-1:] == ['\x1b'] or self.buffer[-2:] == ['\x1b', '[']:
                pass
            else:
                self.display()

    def loop(self) -> None:
        self.go('internal://home')

        self.buffer = []
        self.display()
        while not self.done:
            if self.has_term_control:
                self.read_raw_char()
            else:
                try:
                    command = input(self.get_prompt_str())
                except EOFError:
                    self.done = True
                    break
                self.process_comand(command)
                self.display()

    def loop_with_term_ctrl(self) -> None:
        try:
            import termios, tty
        except ImportError as e:
            return self.loop()

        fd = sys.stdin.fileno()
        old_attributes = termios.tcgetattr(fd)

        try:
            tty.setraw(fd)
            new_attributes = termios.tcgetattr(fd)
            new_attributes[3] |= termios.ISIG # make ctrl-c work
            termios.tcsetattr(fd, termios.TCSADRAIN, new_attributes)
            self.has_term_control = True
            return self.loop()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attributes)

    def load_internal_file(self, name: str) -> GeminiResponse:
        if not re.match(r'^[a-zA-Z_0-9]+$', name):
            return GeminiResponse(59, 'Bad internal URL!', None)

        try:
            with open(f'{name}.gmi', 'r', encoding='utf-8') as f:
                content = f.read()
        except FileNotFoundError:
            return GeminiResponse(51, 'Unknown internal page', None)

        filetered_content = []

        for line in content.split('\n'):
            if m := re.match(r'^&([a-zA-Z_0-9+]+) (.*)$', line):
                filter_type = m.group(1)
                if filter_type == 'if_raw':
                    include = self.has_term_control
                elif filter_type == 'if_canon':
                    include = not self.has_term_control
                else:
                    assert False, 'unrecognised filter type'

                if include:
                    filetered_content.append(m.group(2))
            else:
                filetered_content.append(line)

        return GeminiResponse(
            20, 'text/gemini; charset=utf-8',
            '\n'.join(filetered_content).encode('utf-8')
        )

    def load_history(self) -> GeminiResponse:
        response = ['# History', '']
        for url, name in self.history[::-1]:
            if name:
                response.append(f'=> {url} {name}')
            else:
                response.append(f'=> {url}')
        return GeminiResponse(
            20, 'text/gemini; charset=utf-8',
            '\n'.join(response).encode('utf-8')
        )

    def fetch_browser_page(self, suffix: str) -> GeminiResponse:
        if suffix == 'history':
            return self.load_history()
        else:
            return self.load_internal_file(suffix)

    def error_alert(self, lines: List[str]) -> None:
        self.alert_lines = lines
        self.display()

    def go(self, url: str, override_cache: bool=False) -> None:
        self.error_alert(['Loading...', url])
        self.alert_lines = []

        never_cache = [
            'internal://history'
        ]

        if url in never_cache:
            override_cache = True

        if url in self.page_cache:
            cached_page = self.page_cache[url]
        else:
            cached_page = None

        try:
            if cached_page is None or override_cache:
                if url.startswith('internal://'):
                    cached_page = None
                    response = self.fetch_browser_page(url[len('internal://'):])
                else:
                    cached_page = None
                    response = fetch_gem(url, TrustPolicy())
        except GeminiError as e:
            return self.error_alert(['Error making request! ' + e.message])

        if cached_page is None:
            if response.broad_status() == 1:
                self.more_input_required = (url, response.meta, response.status == 1)
                self.buffer = []
                self.display()
                return
            elif response.broad_status() == 3:
                redirect_to = response.meta
                if not self.looks_like_url(redirect_to):
                    resolved = self.resolve_relative(url, redirect_to, True)
                    if resolved is None:
                        self.error_alert(['Invalid redirect received fro mserver'])
                        return
                    redirect_to = resolved

                def redirect_prompt_done(b: bool) -> None:
                    if b:
                        self.go(redirect_to)
                    else:
                        self.error_alert(['Redirect cancelled'])

                self.yes_no_prompt = (['Redirect request to:', redirect_to], redirect_prompt_done)
                self.display()
                return
            elif response.broad_status() != 2:
                return self.error_alert([
                    f'Not successful: {response.status}: {response.decoded_status()}'
                ] + (['More info: ' + response.meta] if response.broad_status() in (4, 5, 6) else []))

            try:
                mime, body = response.decoded_body_or_err()
            except GeminiError as e:
                return self.error_alert(['Error decoding body! ' + e.message])

            if not mime.startswith('text/'):
                return self.error_alert(['Cannot handle MIME type ' + mime])

            self.page = Page(url, response, self, None)
        else:
            self.page = cached_page

        self.page.render()
        if url != 'internal://history':
            if self.history and self.history[-1][0] == self.page.url:
                pass
            else:
                self.history.append((self.page.url, self.page.name or ''))
        self.page_cache[url] = self.page
        self.display()

    def get_prompt_str(self) -> str:
        if self.more_input_required is not None:
            return ' response >>> '
        if self.yes_no_prompt is not None:
            return ' [y/n] >>> '
        return ' >>> '

    def display(self) -> None:
        if self.more_input_required is not None:
            self.alert_lines = [
                f'User input requested by {self.more_input_required[0]}',
                f'Prompt: {self.more_input_required[1]}',
                f'(leave blank to cancel)'
            ]
        if self.yes_no_prompt is not None:
            self.alert_lines = self.yes_no_prompt[0]

        self.joiner = '\r\n' if self.has_term_control else '\n'
        if self.has_term_control:
            # print(end=self.joiner)
            print(end='\x1b[2J') # clear full screen
            print(end='\x1b[;H') # move cursor to top left
        if self.page: self.page.display()
        print(''.join(
            alert + self.joiner
            for alert in self.alert_lines
        ), end='')
        if self.has_term_control:
            current_buffer = ''.join(
                c if ord(c) >= 32 else '?'
                for c in self.buffer
            )

            if self.more_input_required is not None and self.more_input_required[2]:
                # sensisitve input
                current_buffer = '*' * len(current_buffer)

            prompt_str = self.get_prompt_str()
            print(prompt_str + current_buffer, end='', flush=True)
        if self.page: self.page.status = 'NORMAL'

if __name__ == '__main__':
    browser = Browser('--ascii' not in sys.argv, '--no-colour' not in sys.argv)

    try:
        if '--no-raw' in sys.argv:
            browser.loop()
        else:
            browser.loop_with_term_ctrl()
    except ThemeError as e:
        print('')
        print('Theming error!', e.msg)
    except KeyboardInterrupt:
        print('')
        print('Farewell!')
import argparse
from argparse import *

# Note: This will import everything from the FE argument_parser module, _including_ the ArgumentParser class. We do that
# for convenience for any file importing this file so it has all of the FE utilities available. ArgumentParser will be
# overridden with our modified version below.
from fusion_engine_client.utils.argument_parser import \
    ArgumentParser as ArgumentParserBase
from fusion_engine_client.utils.argument_parser import *


class ArgumentParser(ArgumentParserBase):
    def __init__(self, *args, **kwargs):
        super(ArgumentParser, self).__init__(*args, **kwargs)

    def format_help(self):
        ArgumentParser._set_parser_section_titles(self)
        return super().format_help()

    @staticmethod
    def _set_parser_section_titles(parser: ArgumentParserBase):
        parser._optionals.title = 'Options'
        have_subparsers = False
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                have_subparsers = True
                for p in action._name_parser_map.values():
                    ArgumentParser._set_parser_section_titles(p)

        if have_subparsers:
            parser._positionals.title = 'Commands'
        else:
            parser._positionals.title = 'Positional Arguments'

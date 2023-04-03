import argparse
from argparse import *

from fusion_engine_client.utils.argument_parser import ArgumentParser as ArgumentParserBase
from fusion_engine_client.utils.argument_parser import ExtendedBooleanAction, TriStateBooleanAction


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

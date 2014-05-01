from __future__ import print_function

import calendar
import functools
import json
import logging
import pprint
import re
import sys
from datetime import date
from threading import Lock

import click
import requests


class LoginError(RuntimeError):
    pass


def login_required(f):
    @functools.wraps(f)
    def wrapper(self, *args, **kwargs):
        if not self.logged_in:
            self.login()
        return f(self, *args, **kwargs)
    return wrapper


class Browser(object):

    def __init__(self, username, password):
        self.session = requests.Session()
        self.username = username
        self.password = password
        self.logged_in = False
        self.login_lock = Lock()

    def login(self):
        with self.login_lock:
            if self.logged_in:
                return
            url = ('https://login.olleh.com/wamui/AthWeb.do?'
                   'urlcd=http://www.olleh.com')
            self.session.get(url)
            resp = self.session.post(url, data={'userId': self.username,
                                                'password': self.password})
            if resp.url != 'http://www.olleh.com':
                raise LoginError
                logging.debug('Login failed; response was:')
                logging.debug(resp.content)
            logging.debug('You are now logged in')
            self.logged_in = True
            del self.password

    @login_required
    def get_usage_info(self, month):
        resp = self.session.post(
            'https://my.olleh.com:444/usage/TelTotalUseTimeAjax.action',
            data={'userDate': month.to_date()})
        json = resp.json()
        logging.debug(pprint.pformat(json))
        usage_info = {'month': str(month), 'usage': []}
        try:
            raw_usage_info = list(
                json['freeUseMblBillPrevThsUseQnttListInquiryResponse']
                    ['dst_M_MBL_DRCT_USE_QNTT_LIST'])
        except (KeyError, TypeError):
            logging.debug('No data for %s', month)
            return usage_info
        try:
            usage_info['usage'] = convert_usage_info(raw_usage_info)
        except RuntimeError:
            return
        return usage_info


class Month(object):

    def __init__(self, s):
        try:
            if not re.match(r'^\d{4}-\d{2}$', s):
                raise ValueError
            year = int(s[:4])
            month = int(s[5:7])
            self.d = date(year, month, calendar.monthrange(year, month)[-1])
        except (TypeError, ValueError, calendar.IllegalMonthError):
            raise RuntimeError(('Invalid argument for Month: {!r} '
                                '(must be in YYYY-MM format)').format(s))

    def to_date(self):
        return self.d.strftime('%Y%m%d')

    def next_month(self):
        if self.d.month == 12:
            y, m = self.d.year + 1, 1
        else:
            y, m = self.d.year, self.d.month + 1
        return Month('{:04d}-{:02d}'.format(y, m))

    def __le__(self, other):
        return self.d <= other.d

    def __repr__(self):
        return "Month('{:%Y-%m}')".format(self.d)

    def __str__(self):
        return self.d.strftime('%Y-%m')


type_names = {
    'V': 'voice',
    'D': 'messaging',
    'P': 'data',
}
unit_converters = {
    'voice': lambda n: (n // 60, n % 60),  # (minutes, seconds)
    'data': lambda n: n // 2 // 1024,     # megabytes
}
unit_formatters = {
    'voice': lambda n: '{}m {}s'.format(n[0], n[1]),
    'data': lambda n: '{} MB'.format(n),
}
identity = lambda n: n


def convert_usage_info(raw_info):
    info = []
    for row in raw_info:
        type_ = row.get('bun_GUN')
        if type_ not in type_names:
            raise RuntimeError('Unexpected type: {!r}'.format(type_))
        type_name = type_names[type_]
        converter = unit_converters.get(type_name, identity)
        try:
            d = {
                'type': type_name,
                'label': row['gen_DESC'],
                'quota': converter(int(row['free_MIN_TOTAL'])),
                'used': converter(int(row['free_MIN_USE'])),
            }
        except (KeyError, TypeError, ValueError):
            logging.debug('Unexpected data structure', exc_info=True)
        info.append(d)
    return info


def format_usage_info(info, format):
    if format == 'json':
        return json.dumps(info, indent=2)
    elif format == 'human':
        output = [info['month']]
        if not info['usage']:
            output.append('  No data')
        for row in info['usage']:
            output.append(u'  {} {}'.format(row['type'].capitalize(),
                                            format_quota_used(row)))
        return '\n'.join(output)
    else:
        raise RuntimeError('Unsupported format: {!r}'.format(format))


def format_quota_used(info):
    formatter = unit_formatters.get(info['type'], identity)
    return '{} / {}'.format(formatter(info['used']), formatter(info['quota']))


@click.group()
@click.option('--debug/--no-debug', default=False)
@click.option('--username', help='olleh.com username')
@click.option('--password', help='olleh.com password')
@click.pass_context
def cli(ctx, debug, username, password):
    """Unofficial command line tool for olleh.com. To avoid typing username
    and password every time or to automate the script, you can set environment
    variables OLLEH_USERNAME and OLLEH_PASSWORD. Actually every parameter to
    this tool can be set from environment variables prefixed "OLLEH_".

    """
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    ctx.obj = Browser(username, password)


def catch_login_error(f):
    @functools.wraps(f)
    @click.pass_context
    def wrapper(ctx, *args, **kwargs):
        try:
            return ctx.invoke(f, *args, **kwargs)
        except LoginError:
            print('Login failed (username: {!r})'.format(ctx.obj.username),
                  file=sys.stderr)
            ctx.exit(1)
    return wrapper


@cli.command(short_help='print your mobile phone usage')
@click.option('--month', type=Month, metavar='YYYY-MM',
              default=date.today().strftime('%Y-%m'))
@click.option('--from-month', type=Month, metavar='YYYY-MM')
@click.option('--format', type=click.Choice(['human', 'json']),
              default='human')
@click.pass_obj
@catch_login_error
def usage(browser, month, from_month, format):
    """Prints the usage information for the given month or for the given
    range of months if --from-month is specified. The month must be specified
    in YYYY-MM format (e.g. "2014-05" for May 2014).

    """
    if from_month is not None:
        def generate_formatted_strings():
            m = from_month
            while m <= month:
                yield format_usage_info(browser.get_usage_info(m), format)
                m = m.next_month()            
        if format == 'json':
            print('[')
        for i, formatted in enumerate(generate_formatted_strings()):
            if format == 'json' and i > 0:
                print(',')
            if format == 'json':
                print('\n'.join('  ' + line for line in formatted.split('\n')),
                      end='')
            else:
                print(formatted)
        if format == 'json':
            print('\n]')
    else:
        print(format_usage_info(browser.get_usage_info(month), format))


def main():
    cli(auto_envvar_prefix='OLLEH')


if __name__ == '__main__':
    main()

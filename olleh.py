#! /usr/bin/env python
from __future__ import print_function

import calendar
import functools
import json
import logging
import pprint
import re
import sys
from datetime import date, datetime, timedelta
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

    def __init__(self, username=None, password=None):
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
        logging.debug('url=%s, body=%s', resp.request.url, resp.request.body)
        json = resp.json()
        logging.debug(pprint.pformat(json))
        usage_info = {'month': str(month), 'usage': []}
        usage_list = []
        try:
            raw_usage_info = list(
                json['freeUseMblBillPrevThsUseQnttListInquiryResponse']
                    ['dst_M_MBL_DRCT_USE_QNTT_LIST'])
        except (KeyError, TypeError):
            logging.debug('No data for %s', month)
            return usage_info
        try:
            usage_list = convert_usage_info(raw_usage_info, month)
        except RuntimeError:
            return usage_info
        try:
            raw_usage_info2 = list(
                json['totalUseMblBillPrevThsUseQnttListInquiryResponse']
                    ['dst_M_MBL_DRCT_USE_QNTT_LIST'])
            for row in raw_usage_info2:
                check_for_sanity(row)
        except RuntimeError:
            return usage_info
        except (KeyError, TypeError):
            pass
        usage_info['usage'] = usage_list
        return usage_info


class Month(object):

    def __init__(self, s):
        try:
            if not re.match(r'^\d{4}-\d{2}$', s):
                raise ValueError
            year = int(s[:4])
            month = int(s[5:7])
            self.date = date(year, month, calendar.monthrange(year, month)[-1])
        except (TypeError, ValueError, calendar.IllegalMonthError):
            raise RuntimeError(('Invalid argument for Month: {!r} '
                                '(must be in YYYY-MM format)').format(s))

    def to_date(self):
        return self.date.strftime('%Y%m%d')

    def next_month(self):
        if self.date.month == 12:
            y, m = self.date.year + 1, 1
        else:
            y, m = self.date.year, self.date.month + 1
        return Month('{:04d}-{:02d}'.format(y, m))

    def __le__(self, other):
        return self.date <= other.date

    def __repr__(self):
        return "Month('{:%Y-%m}')".format(self.date)

    def __str__(self):
        return self.date.strftime('%Y-%m')


type_names = {
    'V': 'voice',
    'D': 'messaging',
    'P': 'data',
}
unit_converters = {
    'data': lambda n: n // 2 // 1024,     # megabytes
}
unit_formatters = {
    'voice': lambda n: '{}m {}s'.format(n // 60, n % 60),
    'data': lambda n: '{} MB'.format(n),
}
identity = lambda n: n


def convert_usage_info(raw_info, month):
    info = []
    for row in raw_info:
        check_for_sanity(row, month)
        type_ = row.get('bun_GUN')
        if type_ not in type_names:
            logging.debug('Unexpected type: %r', type_)
            continue
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
            continue
        info.append(d)
    return info


def check_for_sanity(row, month):
    if 'last_CALL_SEIZURE_DT' in row:
        # Sometimes the API server gives incorrect data (gives data for 6
        # months in the future from the given month)
        try:
            dt = datetime.strptime(row['last_CALL_SEIZURE_DT'],
                                   '%Y%m%d%H%M%S')
            if month.date + timedelta(days=30) < dt.date():
                logging.debug(('Requested for data for {}, but got '
                               'last_CALL_SEIZURE_DT {}').format(month, dt))
                raise RuntimeError
        except ValueError:
            pass


def format_usage_info(info, format, show_remaining):
    if format == 'json':
        return json.dumps(info, indent=2)
    elif format == 'human':
        output = [info['month']]
        if not info['usage']:
            output.append('  No data')
        for row in info['usage']:
            output.append(u'  {} {}'.format(row['type'].capitalize(),
                                            format_quota_used(row,
                                                              show_remaining)))
        return '\n'.join(output)
    else:
        raise RuntimeError('Unsupported format: {!r}'.format(format))


def format_quota_used(info, show_remaining):
    formatter = unit_formatters.get(info['type'], identity)
    val = info['used'] if not show_remaining else info['quota'] - info['used']
    return '{} / {}'.format(formatter(val), formatter(info['quota']))


@click.group()
@click.option('--debug/--no-debug', default=False)
def cli(debug):
    """Unofficial command line tool for olleh.com. To avoid typing username
    and password every time or to automate the script, you can set environment
    variables OLLEH_USERNAME and OLLEH_PASSWORD.

    """
    if debug:
        logging.basicConfig(level=logging.DEBUG)


pass_browser = click.make_pass_decorator(Browser, ensure=True)


def browser_option(f):

    def username_callback(ctx, param, value):
        browser = ctx.ensure_object(Browser)
        browser.username = value
        return browser

    def password_callback(ctx, param, value):
        browser = ctx.ensure_object(Browser)
        browser.password = value
        return browser

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

    username_option = click.option('--username', metavar='USERNAME',
                                   envvar='OLLEH_USERNAME',
                                   prompt=True, expose_value=False,
                                   callback=username_callback,
                                   help='olleh.com username')
    password_option = click.option('--password', metavar='PASSWORD',
                                   envvar='OLLEH_PASSWORD',
                                   prompt=True, hide_input=True,
                                   expose_value=False,
                                   callback=password_callback,
                                   help='olleh.com password')
    return username_option(password_option(catch_login_error(f)))


@cli.command(short_help='print your mobile phone usage')
@click.option('--month', type=Month, metavar='YYYY-MM',
              default=date.today().strftime('%Y-%m'))
@click.option('--from-month', type=Month, metavar='YYYY-MM')
@click.option('--format', type=click.Choice(['human', 'json']),
              default='human')
@click.option('--remaining', is_flag=True, default=False,
              help='Show remaining quota instead of used (for --format human)')
@browser_option
@pass_browser
def usage(browser, month, from_month, format, remaining):
    """Prints the usage information for the given month or for the given
    range of months if --from-month is specified. The month must be specified
    in YYYY-MM format (e.g. "2014-05" for May 2014).

    """
    if from_month is not None:
        def generate_formatted_strings():
            m = from_month
            while m <= month:
                yield format_usage_info(browser.get_usage_info(m), format,
                                        remaining)
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
        print(format_usage_info(browser.get_usage_info(month), format,
                                remaining))


def main():
    cli()


if __name__ == '__main__':
    main()

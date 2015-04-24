import time
from decimal import Decimal

try: import simplejson as json
except ImportError: import json

from flask import Blueprint, render_template, request, session, jsonify

from Cornerstone.routes.datastax.cornerstone.rest import get_session

ticker_api = Blueprint('ticker_api', __name__)

cassandra_session = None
prepared_statements = None


def preflight_check():
    global cassandra_session, prepared_statements
    if not cassandra_session:
        cassandra_session = get_session()

        prepared_statements = {}
        prepared_statements['get_user'] = cassandra_session.prepare('''
            SELECT * FROM ticker.user
            WHERE email_address = ?
        ''')
        prepared_statements['update_user'] = cassandra_session.prepare('''
            INSERT INTO ticker.user
                (email_address, risk_tolerance,
                preferred_investment_types, retirement_age, withdrawal_year)
            VALUES
                (?, ?, ?, ?, ?)
        ''')

        prepared_statements['get_history'] = cassandra_session.prepare('''
            SELECT * FROM ticker.history
            WHERE email_address = ?
        ''')
        prepared_statements['update_history'] = cassandra_session.prepare('''
            INSERT INTO ticker.history
                (email_address, date, buy, exchange, symbol, name, price,
                quantity)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?)
        ''')

        prepared_statements['get_portfolio'] = cassandra_session.prepare('''
            SELECT * FROM ticker.portfolio
            WHERE email_address = ?
        ''')
        prepared_statements['update_portfolio'] = cassandra_session.prepare('''
            INSERT INTO ticker.portfolio
                (email_address, exchange, symbol, date, name, buy, price,
                quantity)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?)
        ''')

        prepared_statements['get_recommendations'] = \
            cassandra_session.prepare('''
            SELECT * FROM ticker.recommendations
            WHERE risk_tolerance = ? AND preferred_investment_types = ?
                AND retirement_age = ? AND withdrawal_year = ?
        ''')

        prepared_statements['search_symbol'] = cassandra_session.prepare('''
            SELECT * FROM ticker.quotes
            WHERE solr_query = ?
        ''')

        prepared_statements['get_quote'] = cassandra_session.prepare('''
            SELECT * FROM ticker.quotes
            WHERE exchange = ? AND symbol = ?
            LIMIT 1
        ''')


@ticker_api.route('/')
def index():
    return render_template('datastax/ticker/index.jinja2')


@ticker_api.route('/login', methods=['POST'])
def login():
    session['email_address'] = request.form.get('email_address')

    '''
    GENERATE SEED DATA FOR THIS ACCOUNT!
    '''

    return render_template('datastax/ticker/orbeus.jinja2')


@ticker_api.route('/disclaimer')
def disclaimer():
    return render_template('datastax/ticker/disclaimer.jinja2')


@ticker_api.route('/dash')
def dash():
    return render_template('datastax/ticker/dash.jinja2')


@ticker_api.route('/search', methods=['GET', 'POST'])
def search():
    preflight_check()
    search_term = request.form.get('term', 'CUB')
    solr_query = {
        'q': 'symbol:*{0}*'.format(search_term),
        #  AND name:"~{0}"
        'sort': 'date desc'
    }
    values = {
        'solr_query': json.dumps(solr_query)
    }
    search_results = cassandra_session.execute(
        prepared_statements['search_symbol'].bind(values))

    results = []
    for row in search_results:
        results.append(dict(row))

    print results

    return render_template('datastax/ticker/search.jinja2',
                           results=results)


@ticker_api.route('/customize')
def customize():
    preflight_check()
    values = {
        'email_address': session['email_address']
    }
    user_profile = cassandra_session.execute(
        prepared_statements['get_user'].bind((values)))
    if user_profile:
        user_profile = dict(user_profile[0])
    return render_template('datastax/ticker/customize.jinja2',
                           user_profile=user_profile)


def _get_portfolio(email_address):
    values = {
        'email_address': email_address
    }
    user_history = cassandra_session.execute(
        prepared_statements['get_portfolio'].bind(values))

    results = []
    current_record = None
    for row in user_history:
        if current_record and current_record['symbol'] != row['symbol']:
            results.append(current_record)
            current_record = None
        if not current_record:
            current_record = {
                'exchange': row['exchange'],
                'symbol': row['symbol'],
                'name': row['name'],
                'quantity': 0,
                'investment': 0,
                'last_trade': row['price']
            }
        if row['buy']:
            current_record['quantity'] += row['quantity']
            current_record['investment'] -= row['quantity'] * row['price']
        else:
            current_record['quantity'] -= row['quantity']
            current_record['investment'] += row['quantity'] * row['price']
    results.append(current_record)

    return results


@ticker_api.route('/portfolio', methods=['GET', 'POST'])
def portfolio():
    preflight_check()
    results = _get_portfolio(session['email_address'])

    return render_template('datastax/ticker/portfolio.jinja2',
                           crumb='portfolio',
                           results=results)


@ticker_api.route('/transactions')
def transactions():
    preflight_check()
    values = {
        'email_address': session['email_address']
    }
    user_history = cassandra_session.execute(
        prepared_statements['get_history'].bind(values))

    results = []
    for row in user_history:
        results.append(dict(row))
    return render_template('datastax/ticker/transactions.jinja2',
                           crumb='transactions',
                           results=results)


def _portfolio_hash(email_address):
    portfolio = _get_portfolio(email_address)

    portfolio_hash = {}
    for row in portfolio:
        if not row['exchange'] in portfolio_hash:
            portfolio_hash[row['exchange']] = {}
        if not row['symbol'] in portfolio_hash[row['exchange']]:
            portfolio_hash[row['exchange']][row['symbol']] = row

    return portfolio_hash


@ticker_api.route('/recommendations', methods=['GET', 'POST'])
def recommendations():
    preflight_check()
    if request.method == 'POST':
        values = {
            'email_address': session['email_address'],
            'risk_tolerance': request.form.get('risk_tolerance'),
            'preferred_investment_types': [],
            'retirement_age': request.form.get('retirement_age'),
            'withdrawal_year': request.form.get('withdrawal_year'),
        }

        for key in request.form:
            if 'preferred_investment_types' in key:
                values['preferred_investment_types'].append(key.split(':')[1])

        cassandra_session.execute(
            prepared_statements['update_user'].bind(values))
    else:
        values = {
            'email_address': session['email_address']
        }
        user = cassandra_session.execute(
            prepared_statements['get_user'].bind(values))

        values = {}
        if user:
            row = user[0]
            values = {
                'email_address': row['email_address'],
                'risk_tolerance': row['risk_tolerance'],
                'preferred_investment_types': row['preferred_investment_types'],
                'retirement_age': row['retirement_age'],
                'withdrawal_year': row['withdrawal_year'],
            }

    results = []
    if values:
        # this is how we'll be indexing preferred_investment_types in the
        # recommendations table
        values['preferred_investment_types'].sort()
        values['preferred_investment_types'] = '_'.join(
            values['preferred_investment_types'])
        del values['email_address']

        recommendation_results = cassandra_session.execute(
            prepared_statements['get_recommendations'].bind(values))

        update_date = None
        for row in recommendation_results:
            # only read the latest recommendation update
            if not update_date:
                update_date = row['updated_date']
            if row['updated_date'] != update_date:
                break

            results.append(dict(row))

    return render_template('datastax/ticker/recommendations.jinja2',
                           crumb='recommendations',
                           results=results)


def buy_string_to_bool(string):
    return string.lower() in ('yes', 'true', 't', '1', 'buy')


@ticker_api.route('/buy', methods=['POST'])
def buy():
    preflight_check()
    values = {
        'email_address': session['email_address'],
        'date': request.form.get('date') if request.form.get('date') \
            else time.time() * 1000,
        'buy': buy_string_to_bool(request.form.get('buy')),
        'exchange': request.form.get('exchange'),
        'symbol': request.form.get('symbol'),
        'name': request.form.get('name'),
        'price': Decimal(request.form.get('price')),
        'quantity': Decimal(request.form.get('quantity')),
    }
    cassandra_session.execute(
        prepared_statements['update_history'].bind(values))
    cassandra_session.execute(
        prepared_statements['update_portfolio'].bind(values))
    return jsonify({'status': 'ok'})


@ticker_api.route('/quote')
def quote():
    if not request.args.get('exchange') or not request.args.get('symbol'):
        return jsonify({'error': 'exchange and symbol required.'})
    values = {
        'exchange': request.args.get('exchange'),
        'symbol': request.args.get('symbol'),
    }
    quote = cassandra_session.execute(
        prepared_statements['get_quote'].bind(values))

    results = {}
    if quote:
        results = dict(quote[0])

    return jsonify(results)

"""Evaluation server for Feature Factory user notebooks."""

# flask imports
from functools import wraps
import json
import os
import sys
from urllib.parse import quote
from flask import Flask, redirect, request, Response
import logging
# from jupyterhub.services.auth import HubAuth
from featurefactory.evaluation.future import HubAuth

# Feature Factory imports
import hashlib
import dill
from urllib.parse import unquote_to_bytes
from logging.handlers import RotatingFileHandler
from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound
from featurefactory.evaluation                   import EvaluationResponse, EvaluatorServer
from featurefactory.admin.sqlalchemy_main        import ORMManager
from featurefactory.admin.sqlalchemy_declarative import Feature, Problem, User, Metric
from featurefactory.util                         import get_function, myhash

# setup
prefix = "/services/eval-server"
hub_api_token = os.environ.get("EVAL_API_TOKEN")
hub_api_url = "http://{}:{}/hub/api".format(
    os.environ.get("HUB_CONTAINER_NAME"),
    os.environ.get("HUB_API_PORT")
)
auth = HubAuth(
    api_token            = hub_api_token,
    api_url              = hub_api_url,
    cookie_cache_max_age = 60,
)

# app
app = Flask("eval-server")

def authenticated(f):
    """Decorator for authenticating with the Hub"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # try to authenticate via token
        token_header = request.headers.get('Authorization')
        app.logger.debug("Authenticating via  token '{}' ..."
                .format(token_header))
        if token_header:
            try:
                token_str = token_header[:len("token ")]
                token = token_header[len("token "):]
                user = auth.user_for_token(token)
            except Exception:
                app.logger.exception("Failed to authenticate via token.")
                user = None
        else:
            # try to authenticate via cookie
            cookie = request.cookies.get(auth.cookie_name)
            app.logger.debug("Authenticating via cookie '{}' ..."
                    .format(cookie))
            if cookie:
                user = auth.user_for_cookie(cookie)
            else:
                app.logger.debug("Failed to authenticate via cookie.")
                user = None
        if user:
            app.logger.info("User '{}' authenticated.".format(user["name"]))
            return f(user, *args, **kwargs)
        else:
            app.logger.info("User NOT authenticated.")
            return EvaluationResponse(
                status_code = EvaluationResponse.STATUS_CODE_BAD_AUTH
            )
    return decorated

@app.route(prefix + "/evaluate", methods=["POST"])
@authenticated
def evaluate(user):
    """Process user request to evaluate and register feature.

    Extracts 'database', 'problem_id', 'code', and 'description' from POST
    body.
    """

    try:
        database    = request.form["database"]
        problem_id  = request.form["problem_id"]
        feature_dill= request.form["feature_dill"]
        code        = request.form["code"]
        description = request.form["description"]
    except Exception:
        app.logger.exception("Couldn't read parameters from form.")
        return EvaluationResponse(
            status_code = EvaluationResponse.STATUS_CODE_BAD_REQUEST
        )
    app.logger.debug("Read parameters from form.")

    # preprocessing
    # - look up the problem in the databasse
    # - look up the user in the database
    # - compute the md5 hash of the feature code
    # - convert the feature code into a function
    orm = ORMManager(database, admin=True)
    with orm.session_scope() as session:
        try:
            problem_obj = session.query(Problem)\
                    .filter(Problem.id == problem_id).one()
        except (NoResultFound, MultipleResultsFound) as e:
            app.logger.exception("Couldn't access problem (id '{}') from db"
                    .format(problem_id))
            return EvaluationResponse(
                status_code = EvaluationResponse.STATUS_CODE_BAD_REQUEST
            )
        except Exception:
            app.logger.exception(
                    "Unexpected issue accessing problem (id '{}') from db"
                    .format(problem_id))
            return EvaluationResponse(
                status_code = EvaluationResponse.STATUS_CODE_SERVER_ERROR
            )

        app.logger.debug("Accessed problem (id '{}') from db"
                .format(problem_id))

        user_name = user["name"]
        try:
            user_obj = session.query(User).filter(User.name == user_name).one()
        except (NoResultFound, MultipleResultsFound) as e:
            app.logger.exception("Couldn't access user (name '{}') from db"
                    .format(user_name))
            return EvaluationResponse(
                status_code = EvaluationResponse.STATUS_CODE_BAD_REQUEST
            )
        app.logger.debug("Accessed user (name '{}') from db".format(user_name))

        md5 = myhash(code)
        app.logger.debug("Computed feature hash.")

        evaluator = EvaluatorServer(problem_id, user_name, orm)
        try:
            is_registered = evaluator.check_if_registered(code)
            if is_registered:
                app.logger.debug("feature already registered.")
                return EvaluationResponse(
                    status_code = EvaluationResponse.STATUS_CODE_DUPLICATE_FEATURE
                )
        except Exception:
            app.logger.exception("Unexpected error checking if feature is "
                                 "registered")
            return EvaluationResponse(
                status_code = EvaluationResponse.STATUS_CODE_SERVER_ERROR
            )
        app.logger.debug("Confirmed that feature is not already registered")

        try:
            feature = dill.loads(unquote_to_bytes(feature_dill))
        except Exception:
            app.logger.exception("Couldn't extract function (code '{}')"
                    .format(code))
            return EvaluationResponse(
                    status_code = EvaluationResponse.STATUS_CODE_BAD_FEATURE
            )
        app.logger.debug("Extracted function.")

        # processing
        # - compute the CV score
        # - compute any other metrics
        try:
            metrics = evaluator.evaluate(feature)
            # TODO expand schema
        except ValueError:
            app.logger.exception("Couldn't evaluate feature (code '{}')"
                    .format(code))
            # feature is invalid
            return EvaluationResponse(
                status_code = EvaluationResponse.STATUS_CODE_BAD_FEATURE
            )
        except Exception:
            app.logger.exception(
                    "Unexpected error evaluating feature (code '{}')"
                    .format(code))
            return EvaluationResponse(
                status_code = EvaluationResponse.STATUS_CODE_SERVER_ERROR
            )
        app.logger.debug("Evaluated feature.")

        try:
            # write to db
            feature_obj = Feature(
                description = description,
                code        = code,
                md5         = md5,
                user        = user_obj,
                problem     = problem_obj
            )
            session.add(feature_obj)
            for metric in metrics:
                metric_db = metric.convert(kind="db")
                metric_obj = Metric(
                    feature = feature_obj,
                    name    = metric_db["name"],
                    scoring = metric_db["scoring"],
                    value   = metric_db["value"]
                )
                session.add(metric_obj)

        except Exception:
            app.logger.exception("Unexpected error inserting into db")
            return EvaluationResponse(
                status_code = EvaluationResponse.STATUS_CODE_DB_ERROR
            )
        app.logger.debug("Inserted into db.")

    # return
    # - status code
    # - metrics dict
    return EvaluationResponse(
        status_code=EvaluationResponse.STATUS_CODE_OKAY,
        metrics=metrics
    )

if __name__ == "__main__":
    # set up logging
    log_filename = os.path.join(os.environ.get("FF_DATA_DIR"), "log", "eval-server",
            "eval-server.log")
    if not os.path.exists(os.path.dirname(log_filename)):
        os.makedirs(os.path.dirname(log_filename))
    handler = RotatingFileHandler(log_filename, maxBytes=1024 * 1024 * 5,
            backupCount=5)
    formatter = logging.Formatter("[%(asctime)s] {%(pathname)s:%(lineno)d} "
                                  "%(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG)

    # run app
    host  = "0.0.0.0"
    port  = int(os.environ.get("EVAL_CONTAINER_PORT", 5000))
    debug = bool(os.environ.get("EVAL_FLASK_DEBUG", False))
    app.run(
        host  = host,
        port  = port,
        debug = debug
    )
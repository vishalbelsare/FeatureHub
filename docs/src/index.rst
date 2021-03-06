.. FeatureHub documentation master file, created by
   sphinx-quickstart.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

FeatureHub
===========================================

*FeatureHub* is a web application built on top of JupyterHub and an accompanying Python
package that together facilitate collaborative data science efforts as well as data collection
experiments on those efforts.

The FeatureHub Python package is used to administer a data science collaboration. It
allows users to "register" features in a database backend and discover features written by
other users. These features are automatically scored by the system so that users can see
realtime feedback on the quality of their features.

The FeatureHub app includes a JupyterHub server, containerized Jupyter notebooks for
each user with the FeatureHub package installed, a database backend to store the
features, and data- and user-management tools.

.. toctree::
   :maxdepth: 2
   :caption: Table of Contents

   intro.md
   user-guide.md
   Tutorial.ipynb
   faq.md
   deployment.md

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

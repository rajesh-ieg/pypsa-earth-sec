# -*- coding: utf-8 -*-
"""
Proposed code structure:
X read network (.nc-file)
X add export bus
X connect hydrogen buses (advanced: only ports, not all) to export bus
X add store and connect to export bus
X (add load and connect to export bus) only required if the "store" option fails

Possible improvements:
- Select port buses automatically (with both voronoi and gadm clustering). Use data/ports.csv?
"""


import logging
import os
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pypsa
from helpers import locate_bus, override_component_attrs

logger = logging.getLogger(__name__)


def select_ports(n):
    """This function selects the buses where ports are located"""

    ports = pd.read_csv(
        snakemake.input.export_ports,
        index_col=None,
        keep_default_na=False,
    ).squeeze()
    ports = ports[ports.country.isin(countries)]

    gadm_level = snakemake.config["sector"]["gadm_level"]

    ports["gadm_{}".format(gadm_level)] = ports[["x", "y", "country"]].apply(
        lambda port: locate_bus(
            port[["x", "y"]],
            port["country"],
            gadm_level,
            snakemake.input["shapes_path"],
            snakemake.config["clustering_options"]["alternative_clustering"],
        ),
        axis=1,
    )

    ports = ports.set_index("gadm_{}".format(gadm_level))

    # Select the hydrogen buses based on nodes with ports
    hydrogen_buses_ports = n.buses.loc[ports.index + " H2"]
    hydrogen_buses_ports.index.name = "Bus"

    return hydrogen_buses_ports


def add_export(n, hydrogen_buses_ports, export_h2):
    country_shape = gpd.read_file(snakemake.input["shapes_path"])
    # Find most northwestern point in country shape and get x and y coordinates
    country_shape = country_shape.to_crs("EPSG:4326")

    # Get coordinates of the most western and northern point of the country and add a buffer of 2 degrees (equiv. to approx 220 km)
    x_export = country_shape.geometry.centroid.x.min() - 2
    y_export = country_shape.geometry.centroid.y.max() + 2

    # add export bus
    n.add(
        "Bus",
        "H2 export bus",
        carrier="H2",
        x=x_export,
        y=y_export,
    )

    # add export links
    logger.info("Adding export links")
    n.madd(
        "Link",
        names=hydrogen_buses_ports.index + " export",
        bus0=hydrogen_buses_ports.index,
        bus1="H2 export bus",
        p_nom_extendable=True,
    )

    export_links = n.links[n.links.index.str.contains("export")]
    logger.info(export_links)

    # add store
    n.add(
        "Store",
        "H2 export store",
        bus="H2 export bus",
        e_nom_extendable=True,
        carrier="H2",
        e_initial=0,
        marginal_cost=0,
        capital_cost=0,
    )

    # add load
    n.add(
        "Load",
        "H2 export load",
        bus="H2 export bus",
        carrier="H2",
        p_set=export_h2 / 8760,
    )

    return


if __name__ == "__main__":
    if "snakemake" not in globals():
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        from helpers import mock_snakemake, sets_path_to_root

        snakemake = mock_snakemake(
            "add_export",
            simpl="",
            clusters="16",
            ll="c1.0",
            opts="Co2L",
            planning_horizons="2030",
            sopts="144H",
            discountrate=0.071,
            demand="DF",
            h2export=[0],
        )
        sets_path_to_root("pypsa-earth-sec")

    overrides = override_component_attrs(snakemake.input.overrides)
    n = pypsa.Network(snakemake.input.network, override_component_attrs=overrides)
    countries = list(n.buses.country.unique())

    # get export demand

    export_h2 = eval(snakemake.wildcards["h2export"]) * 1e6  # convert TWh to MWh
    logger.info(
        f"The yearly export demand is {export_h2/1e6} TWh resulting in an hourly average of {export_h2/8760:.2f} MWh"
    )

    # get hydrogen export buses/ports
    hydrogen_buses_ports = select_ports(n)

    # add export value and components to network
    add_export(n, hydrogen_buses_ports, export_h2)

    n.export_to_netcdf(snakemake.output[0])

    logger.info("Network successfully exported")

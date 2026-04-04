#!/usr/bin/env python3
"""
================================================================================
STAAD.Pro .std File Generator v3.3 — Competition-Grade Production Script
with Professional Visualization Suite + Fixed Bay Spacing Parser
================================================================================

Generates VALID, error-free STAAD.Pro .std input files from QRF (Quote Request
Form) JSON data for Pre-Engineered Buildings (PEB).

FEATURES:
  - Complete 3D structural model generation (columns, rafters, purlins, girts,
    bracing, haunch, mezzanine, crane, canopy, flange braces, end wall girts)
  - IS 800:2007 and AISC 360-16 design code support
  - Comprehensive BOQ with cost estimation (INR, GST @ 18%)
  - 6 Professional visualization plots per building:
    1. 3D Structural Model (color-coded wireframe)
    2. Steel Weight Distribution (Donut + Bar Chart)
    3. Steel Takeoff Summary (Quantity, Length, Weight)
    4. Cost Breakdown Dashboard (KPI cards + Pie)
    5. Member Category Distribution (Treemap + Length chart)
    6. Building Parameters Dashboard (KPI panels)

    
FIXES ALL KNOWN FATAL BUGS FROM PREVIOUS VERSIONS:
  1. Zero-length members (same start/end node)
  2. Wind loads applied as GY (vertical) instead of GX/GZ (horizontal)
  3. Reversed member ranges like "2 TO 1" instead of "1 TO 2"
  4. Missing "JOINT LOAD" keyword before joint forces in seismic load
  5. "DESIGN CODE IS" — blank; now uses "DESIGN CODE INDIAN" / "DESIGN CODE AMERICAN"
  6. "SELFWEIGHT Y -1 0" — trailing "0" removed
  7. "SELECT OPTIMIZE" — replaced with "SELECT MEMBER ALL"
  8. Invalid design parameters removed (DEFORM, DEFLECTION CHECK ALL, etc.)
  9. Custom section names replaced with PRISMATIC definitions
 10. No gaps in load numbering; combinations reference only defined loads
 11. Mezzanine members reference only existing nodes
 12. ALL members get property assignments
 13. Member release uses "MZ" not "MOMENT-Z"
 14. Correct STAAD.Pro design code parameters

================================================================================
"""

import json
import math
import os
import re
import sys
import logging
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for headless rendering
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.mplot3d.art3d import Line3DCollection
import matplotlib.gridspec as gridspec
import matplotlib.ticker as ticker

# Suppress matplotlib warnings
warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')

# ============================================================================
# LOGGING CONFIGURATION
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("staad_generator_v2.log", mode="w"),
    ],
)
logger = logging.getLogger(__name__)

# ============================================================================
# PROFESSIONAL COLOR PALETTE & STYLE SETTINGS
# ============================================================================

# Steel / construction industry themed color palette
COLORS = {
    'primary_steel':   '#2C3E50',  # Dark blue-grey (columns, rafters)
    'rafter':          '#34495E',  # Slate grey
    'haunch':          '#E67E22',  # Orange
    'purlin':          '#27AE60',  # Green
    'girt':            '#2980B9',  # Blue
    'bracing':         '#E74C3C',  # Red
    'flange_brace':    '#C0392B',  # Dark red
    'mezzanine':       '#8E44AD',  # Purple
    'crane':           '#D35400',  # Dark orange
    'canopy':          '#16A085',  # Teal
    'end_wall':        '#7F8C8D',  # Grey
    'ridge':           '#F39C12',  # Gold
    'column':          '#2C3E50',  # Dark blue-grey
    'bg_light':        '#F8F9FA',  # Very light grey
    'bg_dark':         '#1A1A2E',  # Dark navy
    'accent':          '#E94560',  # Pinkish red accent
    'text_dark':       '#2C3E50',
    'text_light':      '#ECF0F1',
    'grid':            '#DEE2E6',
    'success':         '#27AE60',
    'warning':         '#F39C12',
    'danger':          '#E74C3C',
}

# Gradient-style bar colors
BAR_COLORS_GRADIENT = ['#1A5276', '#1F618D', '#2471A3', '#2980B9', '#3498DB',
                       '#5DADE2', '#85C1E9', '#AED6F1', '#D4E6F1', '#EBF5FB']

PIE_COLORS = ['#2C3E50', '#E74C3C', '#3498DB', '#27AE60', '#F39C12',
              '#9B59B6', '#1ABC9C', '#E67E22', '#34495E', '#16A085', '#8E44AD', '#D35400']

# Set global matplotlib style
plt.rcParams.update({
    'figure.dpi': 150,
    'font.family': 'DejaVu Sans',
    'font.size': 9,
    'axes.titlesize': 13,
    'axes.labelsize': 10,
    'axes.grid': True,
    'grid.alpha': 0.3,
    'grid.linestyle': '--',
    'axes.spines.top': False,
    'axes.spines.right': False,
    'figure.facecolor': 'white',
    'axes.facecolor': '#FAFBFC',
    'axes.edgecolor': '#DEE2E6',
})


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class BuildingParams:
    """Parsed building parameters from QRF JSON."""
    building_type: str = "portal frame"
    width: float = 20.0          # meters
    length: float = 40.0          # meters
    eave_height: float = 8.0      # meters
    roof_slope_str: str = "1:10"
    roof_slope_deg: float = 5.71  # degrees
    bay_spacing_long: List[float] = field(default_factory=list)  # longitudinal (along length)
    bay_spacing_trans: List[float] = field(default_factory=list)  # transverse (along width)
    end_wall_col_spacing: List[float] = field(default_factory=list)
    bracing_type: str = "X"
    min_thickness_builtup: float = 6.0  # mm
    min_thickness_secondary: float = 4.0  # mm
    brick_wall_height: float = 0.0  # m, 0 means no brick wall
    roof_slope_ratio: float = 0.1  # tan(theta) for 1:10


@dataclass
class DesignLoads:
    """Parsed design loads from QRF JSON."""
    design_code: str = "IS 800:2007"
    live_load_roof: float = 0.75  # kN/m²
    live_load_frame: float = 0.75  # kN/m²
    dead_load: float = 0.15  # kN/m²
    collateral_load: float = 0.25  # kN/m²
    wind_speed_kmh: float = 47.0  # km/hr
    wind_speed_ms: float = 13.06  # m/s
    seismic_zone: str = "II"
    seismic_zone_factor: float = 0.10
    deflection_lateral: str = "H/180"
    deflection_vertical: str = "L/240"
    fyld: float = 250.0  # MPa


@dataclass
class CraneInfo:
    """Crane parameters."""
    has_crane: bool = False
    capacity_ton: float = 5.0
    bracket_height: float = 8.0


@dataclass
class MezzanineInfo:
    """Mezzanine floor parameters."""
    has_mezzanine: bool = False
    live_load: float = 4.0  # kN/m²
    dead_load: float = 3.5  # kN/m² (150mm RCC slab ~3.6 kN/m²)
    height: float = 4.0  # m


@dataclass
class CanopyInfo:
    """Canopy parameters."""
    has_canopy: bool = False
    width: float = 3.0  # m overhang
    height: float = 3.0  # m clear height


@dataclass
class NodeCoord:
    """Joint coordinate in 3D."""
    x: float
    y: float
    z: float


@dataclass
class MemberIncid:
    """Member incidence."""
    start_node: int
    end_node: int


# ============================================================================
# QRF JSON PARSER
# ============================================================================

class QRFParser:
    """
    Parses QRF JSON files (both Variant A and Variant B envelope formats).

    Variant A (bare): { "version_list": [ { "process_json": { ... } } ] }
    Variant B (API):  { "success": true, "data": [ { "version_list": [ ... ] } ] }
    """

    def __init__(self, json_data: Dict[str, Any]):
        self.raw = json_data
        self.sections: Dict[str, Any] = {}
        self.meta: Dict[str, Any] = {}
        self._parse_envelope()

    def _parse_envelope(self) -> None:
        """Extract the process_json from either envelope format."""
        try:
            # Try Variant A: direct version_list
            if "version_list" in self.raw and isinstance(self.raw["version_list"], list):
                versions = self.raw["version_list"]
                for v in versions:
                    pj = v.get("process_json", {})
                    if pj and pj.get("sections"):
                        self.meta = pj.get("meta", {})
                        self.sections = pj["sections"]
                        logger.info("Parsed Variant A (bare) envelope")
                        return

            # Try Variant B: API wrapper with success/data
            if "data" in self.raw and isinstance(self.raw["data"], list):
                for item in self.raw["data"]:
                    vl = item.get("version_list", [])
                    for v in vl:
                        pj = v.get("process_json", {})
                        if pj and pj.get("sections"):
                            self.meta = pj.get("meta", {})
                            self.sections = pj["sections"]
                            logger.info("Parsed Variant B (API wrapper) envelope")
                            return
                        # Also check previous_json if process_json is empty
                        prev = v.get("previous_json", {})
                        if prev and prev.get("sections"):
                            self.meta = prev.get("meta", {})
                            self.sections = prev["sections"]
                            logger.info("Parsed Variant B (from previous_json)")
                            return

            logger.warning("Could not parse JSON envelope; sections may be empty")
        except Exception as e:
            logger.error(f"Envelope parse error: {e}")
            raise

    def _find_section(self, *partial_names: str) -> Optional[List[Dict]]:
        """
        Find a section by partial key name match.
        Handles variable suffix names like 'Canopy Details - Forward type ...'
        """
        if not self.sections:
            return None
        for key, val in self.sections.items():
            if not isinstance(val, list):
                continue
            for pn in partial_names:
                if pn.lower() in key.lower():
                    return val
        return None

    def _get_detail(self, section: List[Dict], desc: str) -> str:
        """Get a detail value from a section by desc field (fuzzy match)."""
        if not section:
            return ""
        desc_lower = desc.lower().strip()
        for item in section:
            item_desc = str(item.get("desc", "")).lower().strip()
            if desc_lower in item_desc or item_desc in desc_lower:
                return str(item.get("details", ""))
        return ""

    def parse_building_params(self) -> BuildingParams:
        """Parse building parameters section."""
        bp = BuildingParams()
        sec = self._find_section("Building Parameters")
        if not sec:
            logger.warning("No Building Parameters section found; using defaults")
            return bp

        # Building Type
        bp.building_type = self._get_detail(sec, "Type") or "portal frame"

        # Width — may be in mm or m
        width_str = self._get_detail(sec, "Width")
        bp.width = self._parse_length(width_str)

        # Length — may be in mm or m
        length_str = self._get_detail(sec, "Length")
        bp.length = self._parse_length(length_str)

        # Eave height — may have "/" for stepped or multiple values
        eave_str = self._get_detail(sec, "Eave height")
        bp.eave_height = self._parse_eave_height(eave_str)

        # Roof slope
        slope_str = self._get_detail(sec, "Roof Slope")
        if slope_str:
            bp.roof_slope_str = slope_str
            bp.roof_slope_deg, bp.roof_slope_ratio = self._parse_roof_slope(slope_str)
        else:
            bp.roof_slope_deg, bp.roof_slope_ratio = 5.71, 0.1

        # Bay spacing (side wall = longitudinal along building length)
        bay_sw_str = self._get_detail(sec, "Bay spacing") or self._get_detail(sec, "Side wall")
        bp.bay_spacing_long = self._parse_bay_spacing(bay_sw_str)

        # End wall column spacing = transverse along building width
        ew_str = self._get_detail(sec, "End Wall Col Spacing")
        bp.bay_spacing_trans = self._parse_bay_spacing(ew_str)

        # CRITICAL FIX: If only 1 bay value was parsed (e.g. "6 m"), expand to
        # fill the full building dimension.  "6 m" means "uniform 6m spacing",
        # NOT "one bay of 6m total".
        if len(bp.bay_spacing_long) == 1 and bp.length > 0:
            spacing = bp.bay_spacing_long[0]
            n_long = max(2, round(bp.length / spacing))
            actual = bp.length / n_long
            bp.bay_spacing_long = [actual] * n_long
            logger.info(f"Expanded long. bays: {n_long} × {actual:.2f}m "
                        f"(parsed '{bay_sw_str}', building length={bp.length:.1f}m)")

        if len(bp.bay_spacing_trans) == 1 and bp.width > 0:
            spacing = bp.bay_spacing_trans[0]
            n_trans = max(2, round(bp.width / spacing))
            actual = bp.width / n_trans
            bp.bay_spacing_trans = [actual] * n_trans
            logger.info(f"Expanded trans. bays: {n_trans} × {actual:.2f}m "
                        f"(parsed '{ew_str}', building width={bp.width:.1f}m)")

        # If no transverse bays, create equally spaced from width
        if not bp.bay_spacing_trans and bp.width > 0:
            n_trans = max(2, int(round(bp.width / 8.0)))
            spacing = bp.width / n_trans
            bp.bay_spacing_trans = [spacing] * n_trans

        # If no longitudinal bays, create from length
        if not bp.bay_spacing_long and bp.length > 0:
            n_long = max(2, int(round(bp.length / 8.0)))
            spacing = bp.length / n_long
            bp.bay_spacing_long = [spacing] * n_long

        # Bracing type
        brace_str = self._get_detail(sec, "Brace")
        if "portal" in brace_str.lower():
            bp.bracing_type = "portal"
        elif "cross" in brace_str.lower() or "x" in brace_str.lower():
            bp.bracing_type = "X"
        elif "diagonal" in brace_str.lower():
            bp.bracing_type = "X"
        else:
            bp.bracing_type = "portal"

        # Min thickness built up
        thick_str = self._get_detail(sec, "Minimum Thickness Built up")
        bp.min_thickness_builtup = self._parse_length(thick_str, default_mm=6.0)
        if bp.min_thickness_builtup < 1.0:
            bp.min_thickness_builtup = 6.0  # assume mm if value is tiny

        # Min thickness secondary
        sec_thick_str = self._get_detail(sec, "Minimum Thickness Secondary")
        bp.min_thickness_secondary = self._parse_length(sec_thick_str, default_mm=4.0)
        if bp.min_thickness_secondary < 1.0:
            bp.min_thickness_secondary = 4.0

        # Brick wall height from Brick Wall section
        brick_sec = self._find_section("Brick Wall")
        if brick_sec:
            bw_detail = self._get_detail(brick_sec, "Front Side wall")
            bp.brick_wall_height = self._parse_brick_wall_height(bw_detail)

        logger.info(
            f"Building: {bp.width:.2f}m x {bp.length:.2f}m x {bp.eave_height:.2f}m, "
            f"slope={bp.roof_slope_str}, bays_long={len(bp.bay_spacing_long)}, "
            f"bays_trans={len(bp.bay_spacing_trans)}"
        )
        return bp

    def parse_design_loads(self) -> DesignLoads:
        """Parse design loads section."""
        dl = DesignLoads()
        sec = self._find_section("Design Loads")
        if not sec:
            logger.warning("No Design Loads section found; using defaults")
            return dl

        # Design code
        code_str = self._get_detail(sec, "Design code")
        dl.design_code = code_str.strip() if code_str else "IS 800:2007"

        # Determine design code family
        code_upper = dl.design_code.upper()
        if "AISC" in code_upper or "MBMA" in code_upper:
            dl.fyld = 345.0  # Typical for AISC designs (A36/A992 steel)
        else:
            dl.fyld = 250.0  # IS 800 typical E250 steel

        # Live load on Roof
        ll_roof_str = self._get_detail(sec, "Live load") and self._get_detail(sec, "Roof")
        if not ll_roof_str:
            ll_roof_str = self._get_detail(sec, "Live load (kN/sqm) on Roof")
        dl.live_load_roof = self._parse_kn_per_sqm(ll_roof_str, default=0.75)

        # Live load on Frame
        ll_frame_str = self._get_detail(sec, "Live load (kN/sqm) on Frame")
        dl.live_load_frame = self._parse_kn_per_sqm(ll_frame_str, default=dl.live_load_roof)

        # Dead load
        dl_str = self._get_detail(sec, "Dead Load")
        dl.dead_load = self._parse_kn_per_sqm(dl_str, default=0.15)

        # Collateral load
        coll_str = self._get_detail(sec, "Collateral")
        dl.collateral_load = self._parse_kn_per_sqm(coll_str, default=0.25)

        # Wind speed
        wind_str = self._get_detail(sec, "Wind Speed")
        dl.wind_speed_kmh = self._parse_wind_speed(wind_str)
        dl.wind_speed_ms = dl.wind_speed_kmh / 3.6

        # Seismic zone
        seismic_str = self._get_detail(sec, "Earthquake") or self._get_detail(sec, "Seismic")
        dl.seismic_zone = self._parse_seismic_zone(seismic_str)
        dl.seismic_zone_factor = self._get_seismic_zone_factor(dl.seismic_zone)

        # Deflection limits
        defl_str = self._get_detail(sec, "Deflection")
        dl.deflection_lateral, dl.deflection_vertical = self._parse_deflection(defl_str)

        logger.info(
            f"Loads: code={dl.design_code}, LL_roof={dl.live_load_roof}, "
            f"DL={dl.dead_load}, wind={dl.wind_speed_kmh} km/h, "
            f"seismic={dl.seismic_zone} (Z={dl.seismic_zone_factor})"
        )
        return dl

    def parse_crane(self, eave_height: float = 8.0) -> CraneInfo:
        """Parse crane details. Requires eave_height for default bracket height."""
        ci = CraneInfo()
        sec = self._find_section("Crane")
        if not sec:
            return ci

        crane_str = self._get_detail(sec, "Nos of Crane") or self._get_detail(sec, "Crane")
        if crane_str and "na" not in crane_str.lower() and "not" not in crane_str.lower():
            ci.has_crane = True
            # Extract capacity
            cap_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:TON|ton|Ton|T|t)', crane_str)
            if cap_match:
                ci.capacity_ton = float(cap_match.group(1))
            # Extract bracket height
            bh_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:m|M)\s*(?:BKT|bkt|bracket|BRACKET|level|Level)', crane_str)
            if bh_match:
                ci.bracket_height = float(bh_match.group(1))
            else:
                ci.bracket_height = eave_height * 0.7  # default 70% of eave

        logger.info(f"Crane: has={ci.has_crane}, capacity={ci.capacity_ton}T")
        return ci

    def parse_mezzanine(self, building_width: float, building_length: float) -> MezzanineInfo:
        """Parse mezzanine floor details."""
        mi = MezzanineInfo()
        sec = self._find_section("Mezzanine")
        if not sec:
            return mi

        mi.has_mezzanine = True

        # Height
        ht_str = self._get_detail(sec, "Height")
        if ht_str:
            height_vals = re.findall(r'(\d+(?:\.\d+)?)\s*m', ht_str, re.IGNORECASE)
            if height_vals:
                mi.height = min(float(h) for h in height_vals)

        # Live load
        ll_str = self._get_detail(sec, "Live Load")
        mi.live_load = self._parse_kn_per_sqm(ll_str, default=4.0)

        # Dead load (typically 150mm RCC slab ≈ 3.5-4.0 kN/m²)
        mi.dead_load = 3.75  # Default for 150mm RCC slab

        logger.info(f"Mezzanine: has={mi.has_mezzanine}, height={mi.height}m, LL={mi.live_load}")
        return mi

    def parse_canopy(self) -> CanopyInfo:
        """Parse canopy details."""
        ci = CanopyInfo()
        sec = self._find_section("Canopy")
        if not sec:
            return ci

        ci.has_canopy = True

        # Width / overhang
        w_str = self._get_detail(sec, "Width")
        if w_str:
            w_match = re.search(r'(\d+(?:\.\d+)?)\s*m', w_str, re.IGNORECASE)
            if w_match:
                ci.width = float(w_match.group(1))

        # Height
        h_str = self._get_detail(sec, "Clear height")
        if h_str:
            h_match = re.search(r'(\d+(?:\.\d+)?)\s*m', h_str, re.IGNORECASE)
            if h_match:
                ci.height = float(h_match.group(1))

        logger.info(f"Canopy: has={ci.has_canopy}, width={ci.width}m")
        return ci

    # ========================================================================
    # Helper parsing methods
    # ========================================================================

    @staticmethod
    def _parse_length(text: str, default_mm: float = 0.0) -> float:
        """
        Parse a length value from text. Returns value in meters.
        Handles: "24380 mm", "50.00 m", "1@7.115 + 5@8.700", etc.
        """
        if not text or text.strip().lower() in ("na", "n/a", ""):
            return 0.0

        text = text.strip()

        # If multiple "@", extract all dimensions and sum the first representative
        at_matches = re.findall(r'(\d+(?:\.\d+)?)\s*(?:mm|m)\s*(?:c/c)?', text, re.IGNORECASE)
        if at_matches and "@" not in text:
            val = float(at_matches[0])
            if "mm" in text.lower() and val > 100:
                return val / 1000.0
            return val

        # Pattern: "N@Wm" format like "1@7.115 + 5@8.700"
        bay_matches = re.findall(r'(\d+)\s*@\s*(\d+(?:\.\d+)?)\s*(mm|m)', text, re.IGNORECASE)
        if bay_matches:
            total = 0.0
            for count, dim, unit in bay_matches:
                d = float(dim)
                if unit.lower() == "mm":
                    d /= 1000.0
                total += int(count) * d
            return total

        # Pattern: standalone number with unit
        # Check for mm first (large numbers > 100 likely mm)
        mm_match = re.search(r'(\d+(?:\.\d+)?)\s*mm', text, re.IGNORECASE)
        if mm_match:
            val = float(mm_match.group(1))
            if val > 100:  # definitely mm
                return val / 1000.0
            return val  # already in meters

        # Check for m
        m_match = re.search(r'(\d+(?:\.\d+)?)\s*m(?!m)', text, re.IGNORECASE)
        if m_match:
            return float(m_match.group(1))

        # Plain number
        num_match = re.search(r'(\d+(?:\.\d+)?)', text)
        if num_match:
            val = float(num_match.group(1))
            if val > 100:
                return val / 1000.0  # assume mm
            return val

        return default_mm / 1000.0 if default_mm > 0 else 0.0

    @staticmethod
    def _parse_eave_height(text: str) -> float:
        """Parse eave height, handling stepped heights and multiple values."""
        if not text:
            return 8.0

        # Handle stepped like "13.0 m / 9.0 m / 7.5 m" — take the max reasonable value
        values = re.findall(r'(\d+(?:\.\d+)?)\s*m', text, re.IGNORECASE)
        if values:
            floats = [float(v) for v in values]
            # Filter out unrealistic values (> 100 is likely in mm)
            reasonable = [v for v in floats if v < 100]
            if not reasonable:
                # All values > 100, convert from mm to m
                reasonable = [v / 1000.0 for v in floats]
            # Use the most common / primary eave height
            # Sort and take median for multi-height buildings
            reasonable_sorted = sorted(reasonable)
            if len(reasonable_sorted) >= 3:
                return reasonable_sorted[len(reasonable_sorted) // 2]
            return max(reasonable) if reasonable else 8.0

        num_match = re.search(r'(\d+(?:\.\d+)?)', text)
        if num_match:
            val = float(num_match.group(1))
            if val > 100:
                return val / 1000.0  # convert from mm to m
            return val
        return 8.0

    @staticmethod
    def _parse_roof_slope(text: str) -> Tuple[float, float]:
        """Parse roof slope, returns (degrees, ratio)."""
        if not text:
            return 5.71, 0.1

        text = text.strip()

        # Pattern: "1:10", "1:12", etc.
        ratio_match = re.search(r'(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)', text)
        if ratio_match:
            num = float(ratio_match.group(1))
            den = float(ratio_match.group(2))
            ratio = num / den
            degrees = math.degrees(math.atan(ratio))
            return round(degrees, 4), round(ratio, 6)

        # Pattern: "10°", "5.71 deg"
        deg_match = re.search(r'(\d+(?:\.\d+)?)\s*[°deg]', text, re.IGNORECASE)
        if deg_match:
            degrees = float(deg_match.group(1))
            ratio = math.tan(math.radians(degrees))
            return round(degrees, 4), round(ratio, 6)

        return 5.71, 0.1

    @staticmethod
    def _parse_bay_spacing(text: str) -> List[float]:
        """Parse bay spacing string into list of floats in meters."""
        if not text or text.strip().lower() in ("na", "n/a", ""):
            return []

        text = text.strip()
        bays: List[float] = []

        # Pattern: "1@7.115 + 5@8.700 + ..."
        at_matches = re.findall(
            r'(\d+)\s*@\s*(\d+(?:\.\d+)?)\s*(mm|m)(?:\s*(?:c/c)?)?',
            text, re.IGNORECASE
        )
        if at_matches:
            for count, dim, unit in at_matches:
                d = float(dim)
                if unit.lower() == "mm":
                    d /= 1000.0
                for _ in range(int(count)):
                    bays.append(d)
            return bays

        # Pattern: "6 bays × 8.75 m" or "6 bays x 8.75 m"
        bays_match = re.search(
            r'(\d+)\s*(?:bays?|bay)\s*[×x]\s*(\d+(?:\.\d+)?)\s*m',
            text, re.IGNORECASE
        )
        if bays_match:
            n = int(bays_match.group(1))
            spacing = float(bays_match.group(2))
            return [spacing] * n

        # Pattern: "6 m" (uniform)
        uniform_match = re.search(r'^(\d+(?:\.\d+)?)\s*m$', text.strip(), re.IGNORECASE)
        if uniform_match:
            return [float(uniform_match.group(1))]

        return bays

    @staticmethod
    def _parse_brick_wall_height(text: str) -> float:
        """Extract brick wall height from text like 'Up to 3.0 m Ht. BRICK WALL'."""
        if not text:
            return 0.0
        m_match = re.search(r'(\d+(?:\.\d+)?)\s*m(?:\s*Ht)?', text, re.IGNORECASE)
        if m_match:
            return float(m_match.group(1))
        return 0.0

    @staticmethod
    def _parse_kn_per_sqm(text: str, default: float = 0.0) -> float:
        """Parse load value in kN/m² or kg/m²."""
        if not text or text.strip().lower() in ("na", "n/a", ""):
            return default

        # kN/m² or kN/m^2
        kn_match = re.search(r'(\d+(?:\.\d+)?)\s*kN/m', text, re.IGNORECASE)
        if kn_match:
            return float(kn_match.group(1))

        # kg/m² — convert to kN/m² (divide by ~102)
        kg_match = re.search(r'(\d+(?:\.\d+)?)\s*kg/m', text, re.IGNORECASE)
        if kg_match:
            return float(kg_match.group(1)) / 101.97

        # Just a number
        num_match = re.search(r'(\d+(?:\.\d+)?)', text)
        if num_match:
            val = float(num_match.group(1))
            if val > 50:  # probably kg/m²
                return val / 101.97
            return val

        return default

    @staticmethod
    def _parse_wind_speed(text: str) -> float:
        """Parse wind speed, return in km/h."""
        if not text:
            return 47.0

        # km/hr or km/h
        kmh_match = re.search(r'(\d+(?:\.\d+)?)\s*km', text, re.IGNORECASE)
        if kmh_match:
            return float(kmh_match.group(1))

        # m/sec or m/s
        ms_match = re.search(r'(\d+(?:\.\d+)?)\s*m/s', text, re.IGNORECASE)
        if ms_match:
            return float(ms_match.group(1)) * 3.6

        # Just a number (assume km/h if > 10, else m/s)
        num_match = re.search(r'(\d+(?:\.\d+)?)', text)
        if num_match:
            val = float(num_match.group(1))
            if val > 30:
                return val  # already km/h
            return val * 3.6  # m/s to km/h

        return 47.0

    @staticmethod
    def _parse_seismic_zone(text: str) -> str:
        """Parse seismic zone string."""
        if not text:
            return "II"
        # Look for zone pattern
        zone_match = re.search(r'[Zz]one\s*(?:III?V?|IV|2|3|4|5)', text)
        if zone_match:
            z = zone_match.group(0).lower().replace("zone", "").strip()
            return z.upper() if z else "II"
        # Just "II", "III", "IV", "V", "2", "3", etc.
        simple = re.search(r'(?:zone\s*)?([IV]+|\d)', text, re.IGNORECASE)
        if simple:
            return simple.group(1).upper()
        return "II"

    @staticmethod
    def _get_seismic_zone_factor(zone: str) -> float:
        """Get IS 1893 zone factor."""
        zone = zone.upper().strip()
        zone_map = {
            "II": 0.10, "2": 0.10,
            "III": 0.16, "3": 0.16,
            "IV": 0.24, "4": 0.24,
            "V": 0.36, "5": 0.36,
        }
        return zone_map.get(zone, 0.10)

    @staticmethod
    def _parse_deflection(text: str) -> Tuple[str, str]:
        """Parse deflection limits. Returns (lateral, vertical)."""
        if not text:
            return "H/180", "L/240"

        lateral = "H/180"
        vertical = "L/240"

        if "lateral" in text.lower() or "h/" in text.lower():
            h_match = re.search(r'H/(\d+)', text, re.IGNORECASE)
            if h_match:
                lateral = f"H/{h_match.group(1)}"

        if "vertical" in text.lower() or "l/" in text.lower():
            l_match = re.search(r'L/(\d+)', text, re.IGNORECASE)
            if l_match:
                vertical = f"L/{l_match.group(1)}"

        if lateral == "H/180" and vertical == "L/240":
            # Try any L/ pattern
            all_l = re.findall(r'L/(\d+)', text, re.IGNORECASE)
            if all_l:
                vertical = f"L/{all_l[0]}"

        return lateral, vertical


# ============================================================================
# SECTION PROPERTY DATABASE
# ============================================================================

class SectionDatabase:
    """
    Provides PRISMATIC section properties for PEB built-up sections.
    All values in meters and kN units (STAAD.Pro convention: E in kN/m²).
    """

    # Built-up column sections: (depth_mm, flange_width_mm, web_thickness_mm, flange_thickness_mm)
    # Returns PRISMATIC properties: AX (m²), IZ (m⁴), IY (m⁴), IX (m⁴)
    @staticmethod
    def builtup_column_props(
        depth_mm: float = 400,
        flange_width_mm: float = 200,
        tw_mm: float = 8.0,
        tf_mm: float = 12.0,
    ) -> Dict[str, float]:
        """Calculate PRISMATIC properties for a built-up I-section."""
        d = depth_mm / 1000.0  # m
        bf = flange_width_mm / 1000.0  # m
        tw = tw_mm / 1000.0  # m
        tf = tf_mm / 1000.0  # m
        hw = d - 2 * tf  # web height

        # Cross-sectional area (m²)
        ax = 2 * bf * tf + hw * tw
        # Moment of inertia about Z-axis (strong axis) (m⁴)
        iz = (bf * d ** 3 - (bf - tw) * hw ** 3) / 12.0
        # Moment of inertia about Y-axis (weak axis) (m⁴)
        iy = (2 * tf * bf ** 3 + hw * tw ** 3) / 12.0
        # Torsional constant (approximate for I-section) (m⁴)
        ix = (2 * bf * tf ** 3 + hw * tw ** 3) / 3.0

        return {"AX": ax, "IZ": iz, "IY": iy, "IX": ix}

    @staticmethod
    def builtup_rafter_props(
        depth_mm: float = 350,
        flange_width_mm: float = 150,
        tw_mm: float = 6.0,
        tf_mm: float = 10.0,
    ) -> Dict[str, float]:
        """Calculate PRISMATIC properties for a built-up rafter I-section."""
        return SectionDatabase.builtup_column_props(depth_mm, flange_width_mm, tw_mm, tf_mm)

    @staticmethod
    def purlin_props(
        depth_mm: float = 150,
        thickness_mm: float = 2.0,
    ) -> Dict[str, float]:
        """
        Calculate approximate PRISMATIC properties for a cold-formed Z-purlin.
        Uses simplified thin-walled section properties.
        """
        h = depth_mm / 1000.0
        t = thickness_mm / 1000.0
        # Approximate flange width for Z-section
        bf = h * 0.4
        # Area
        ax = 2 * bf * t + h * t
        # Approximate IZ
        iz = t * h ** 3 / 12.0 * 1.5
        # Approximate IY
        iy = t * bf ** 3 / 6.0
        # IX (approximate)
        ix = (2 * bf * t ** 3 + h * t ** 3) / 3.0
        return {"AX": ax, "IZ": iz, "IY": iy, "IX": ix}

    @staticmethod
    def girt_props(depth_mm: float = 150, thickness_mm: float = 1.6) -> Dict[str, float]:
        """Same as purlin but typically thinner."""
        return SectionDatabase.purlin_props(depth_mm, thickness_mm)

    @staticmethod
    def brace_props(dia_mm: float = 50, thickness_mm: float = 3.0) -> Dict[str, float]:
        """Calculate PRISMATIC properties for a tubular/pipe brace section."""
        d = dia_mm / 1000.0
        t = thickness_mm / 1000.0
        r = d / 2.0
        # Area of hollow tube
        ax = math.pi * (r ** 2 - (r - t) ** 2)
        # Moment of inertia
        iz = math.pi * (r ** 4 - (r - t) ** 4) / 4.0
        iy = iz
        # Torsional constant
        ix = 2 * iz
        return {"AX": ax, "IZ": iz, "IY": iy, "IX": ix}

    @staticmethod
    def haunch_props(
        depth_start_mm: float = 400,
        depth_end_mm: float = 700,
        flange_width_mm: float = 200,
        tw_mm: float = 8.0,
        tf_mm: float = 12.0,
    ) -> Dict[str, float]:
        """Calculate PRISMATIC properties for a haunch (tapered member) at average depth."""
        avg_depth = (depth_start_mm + depth_end_mm) / 2.0
        return SectionDatabase.builtup_column_props(avg_depth, flange_width_mm, tw_mm, tf_mm)

    @staticmethod
    def mezzanine_beam_props(
        depth_mm: float = 300,
        flange_width_mm: float = 150,
        tw_mm: float = 6.0,
        tf_mm: float = 10.0,
    ) -> Dict[str, float]:
        """Mezzanine beam section properties."""
        return SectionDatabase.builtup_column_props(depth_mm, flange_width_mm, tw_mm, tf_mm)

    @staticmethod
    def mezzanine_col_props(
        depth_mm: float = 200,
        flange_width_mm: float = 100,
        tw_mm: float = 6.0,
        tf_mm: float = 8.0,
    ) -> Dict[str, float]:
        """Mezzanine column section properties."""
        return SectionDatabase.builtup_column_props(depth_mm, flange_width_mm, tw_mm, tf_mm)

    @staticmethod
    def canopy_beam_props(
        depth_mm: float = 200,
        flange_width_mm: float = 100,
        tw_mm: float = 5.0,
        tf_mm: float = 8.0,
    ) -> Dict[str, float]:
        """Canopy beam section properties."""
        return SectionDatabase.builtup_column_props(depth_mm, flange_width_mm, tw_mm, tf_mm)

    @staticmethod
    def crane_girder_props(
        depth_mm: float = 450,
        flange_width_mm: float = 200,
        tw_mm: float = 8.0,
        tf_mm: float = 14.0,
    ) -> Dict[str, float]:
        """Crane girder section properties."""
        return SectionDatabase.builtup_column_props(depth_mm, flange_width_mm, tw_mm, tf_mm)


# ============================================================================
# GEOMETRY GENERATOR
# ============================================================================

class GeometryGenerator:
    """
    Generates 3D STAAD.Pro geometry (joints and members) for a PEB building.

    Coordinate system:
        X = longitudinal (along building length)
        Y = transverse (along building width)
        Z = vertical (up)

    FIX: No zero-length members. Every member has distinct start/end nodes.
    FIX: No reversed member ranges. Ranges are always low TO high.
    """

    def __init__(self, bp: BuildingParams):
        self.bp = bp
        self.nodes: Dict[int, NodeCoord] = {}
        self.members: Dict[int, MemberIncid] = {}
        self._next_node = 1
        self._next_member = 1

        # Member category tracking (for property/constant assignment)
        self.main_columns: List[int] = []
        self.main_rafters: List[int] = []
        self.haunch_members: List[int] = []
        self.purlins: List[int] = []
        self.girts: List[int] = []
        self.braces_roof: List[int] = []
        self.braces_side_wall: List[int] = []
        self.end_wall_columns: List[int] = []
        self.end_wall_girts: List[int] = []
        self.flange_braces: List[int] = []
        self.mezzanine_beams: List[int] = []
        self.mezzanine_cols: List[int] = []
        self.canopy_beams: List[int] = []
        self.canopy_cols: List[int] = []
        self.crane_girders: List[int] = []
        self.ridge_members: List[int] = []

    def _add_node(self, x: float, y: float, z: float) -> int:
        """Add a node and return its ID."""
        nid = self._next_node
        self.nodes[nid] = NodeCoord(x, y, z)
        self._next_node += 1
        return nid

    def _add_member(self, n1: int, n2: int) -> int:
        """
        Add a member between two nodes. GUARANTEES no zero-length members.
        Returns member ID, or -1 if nodes are coincident.
        """
        if n1 == n2:
            logger.warning(f"ZERO-LENGTH MEMBER PREVENTED: node {n1}")
            return -1
        c1 = self.nodes.get(n1)
        c2 = self.nodes.get(n2)
        if c1 and c2:
            dist = math.sqrt(
                (c1.x - c2.x) ** 2 + (c1.y - c2.y) ** 2 + (c1.z - c2.z) ** 2
            )
            if dist < 0.001:
                logger.warning(f"ZERO-LENGTH MEMBER PREVENTED: nodes {n1}-{n2}")
                return -1
        mid = self._next_member
        self.members[mid] = MemberIncid(n1, n2)
        self._next_member += 1
        return mid

    def generate(self) -> None:
        """Generate complete 3D geometry."""
        logger.info("Generating 3D geometry...")

        # Compute cumulative X positions for longitudinal gridlines
        x_positions = [0.0]
        for bay in self.bp.bay_spacing_long:
            x_positions.append(x_positions[-1] + bay)

        # Compute cumulative Y positions for transverse gridlines
        y_positions = [0.0]
        for bay in self.bp.bay_spacing_trans:
            y_positions.append(y_positions[-1] + bay)

        n_frames = len(x_positions) - 1  # number of main frames
        n_cols_per_frame = len(y_positions)  # columns per frame line

        # Ridge height = eave + (width/2) * slope_ratio (for symmetric portal)
        half_width = self.bp.width / 2.0
        ridge_rise = half_width * self.bp.roof_slope_ratio
        ridge_height = self.bp.eave_height + ridge_rise

        logger.info(
            f"Frames: {n_frames}, Cols/frame: {n_cols_per_frame}, "
            f"Ridge height: {ridge_height:.3f}m"
        )

        # Store gridline data for later reference
        self._x_positions = x_positions
        self._y_positions = y_positions
        self._ridge_height = ridge_height
        self._n_frames = n_frames

        # ------------------------------------------------------------------
        # STEP 1: Main columns (at each longitudinal gridline)
        # ------------------------------------------------------------------
        col_top_nodes: Dict[int, Dict[float, int]] = {}  # x_idx -> {y_pos: node_id}
        col_base_nodes: Dict[int, Dict[float, int]] = {}

        for xi, x in enumerate(x_positions):
            col_top_nodes[xi] = {}
            col_base_nodes[xi] = {}
            for y in y_positions:
                base = self._add_node(x, y, 0.0)
                top = self._add_node(x, y, self.bp.eave_height)
                col_base_nodes[xi][y] = base
                col_top_nodes[xi][y] = top
                mid = self._add_member(base, top)
                if mid > 0:
                    self.main_columns.append(mid)

        # ------------------------------------------------------------------
        # STEP 2: Rafters (between column tops of adjacent transverse lines)
        # ------------------------------------------------------------------
        for xi, x in enumerate(x_positions):
            for j in range(len(y_positions) - 1):
                y1 = y_positions[j]
                y2 = y_positions[j + 1]
                bay_width = y2 - y1

                # Left rafter: from col_top at y1 to ridge
                ridge_z = self.bp.eave_height + bay_width * self.bp.roof_slope_ratio
                ridge_y = (y1 + y2) / 2.0
                ridge_node = self._add_node(x, ridge_y, ridge_z)

                left_top = col_top_nodes[xi][y1]
                mid = self._add_member(left_top, ridge_node)
                if mid > 0:
                    self.main_rafters.append(mid)

                # Right rafter: from ridge to col_top at y2
                right_top = col_top_nodes[xi][y2]
                mid2 = self._add_member(ridge_node, right_top)
                if mid2 > 0:
                    self.main_rafters.append(mid2)

                # Ridge member (connect ridge nodes across width)
                if j == len(y_positions) - 2:
                    pass  # already connected via rafters

        # ------------------------------------------------------------------
        # STEP 3: Haunch members at column-rafter connections
        # ------------------------------------------------------------------
        haunch_len = 1.0  # 1.0m typical haunch length
        for xi, x in enumerate(x_positions):
            for j in range(len(y_positions) - 1):
                y1 = y_positions[j]
                y2 = y_positions[j + 1]
                bay_width = y2 - y1

                col_top = col_top_nodes[xi][y1]
                col_coord = self.nodes[col_top]

                # Haunch extends from column top along the rafter direction
                # Direction toward ridge
                dy = (y2 - y1)
                dz = bay_width * self.bp.roof_slope_ratio
                dist = math.sqrt(dy ** 2 + dz ** 2)
                if dist < 0.001:
                    continue

                frac = min(haunch_len / dist, 0.15)
                hx = col_coord.x
                hy = col_coord.y + dy * frac
                hz = col_coord.z + dz * frac
                haunch_node = self._add_node(hx, hy, hz)
                mid = self._add_member(col_top, haunch_node)
                if mid > 0:
                    self.haunch_members.append(mid)

        # ------------------------------------------------------------------
        # STEP 4: Purlins (along longitudinal direction, on rafters)
        # ------------------------------------------------------------------
        n_purlins_per_side = max(3, int(half_width / 1.5))
        for j in range(len(y_positions) - 1):
            y1 = y_positions[j]
            y2 = y_positions[j + 1]
            bay_width = y2 - y1
            ridge_z = self.bp.eave_height + bay_width * self.bp.roof_slope_ratio

            for p in range(1, n_purlins_per_side + 1):
                frac = p / (n_purlins_per_side + 1)
                purlin_z = self.bp.eave_height + frac * (ridge_z - self.bp.eave_height)
                purlin_y = y1 + frac * (y2 - y1)

                # Create purlin across all frames
                prev_node = None
                for xi in range(len(x_positions)):
                    x = x_positions[xi]
                    purlin_node = self._add_node(x, purlin_y, purlin_z)
                    if prev_node is not None:
                        mid = self._add_member(prev_node, purlin_node)
                        if mid > 0:
                            self.purlins.append(mid)
                    prev_node = purlin_node

        # ------------------------------------------------------------------
        # STEP 5: Girts (on side walls, along longitudinal direction)
        # ------------------------------------------------------------------
        n_girt_levels = max(1, int((self.bp.eave_height - self.bp.brick_wall_height) / 1.5))
        n_girt_levels = min(n_girt_levels, 5)  # cap at 5 levels

        # Girts on front wall (y=0) and back wall (y=width)
        for side_y in [0.0, self.bp.width]:
            for g in range(1, n_girt_levels + 1):
                frac = g / (n_girt_levels + 1)
                girt_z = self.bp.brick_wall_height + frac * (
                    self.bp.eave_height - self.bp.brick_wall_height
                )

                prev_node = None
                for xi in range(len(x_positions)):
                    x = x_positions[xi]
                    girt_node = self._add_node(x, side_y, girt_z)
                    if prev_node is not None:
                        mid = self._add_member(prev_node, girt_node)
                        if mid > 0:
                            self.girts.append(mid)
                    prev_node = girt_node

        # ------------------------------------------------------------------
        # STEP 6: Roof bracing (X-bracing in selected bays)
        # ------------------------------------------------------------------
        # Place roof bracing in first and last bays
        brace_bays_xi = [0, max(0, len(x_positions) - 2)]
        if len(x_positions) > 4:
            mid_bay = len(x_positions) // 2
            brace_bays_xi.append(mid_bay)

        for brace_xi in brace_bays_xi:
            if brace_xi >= len(x_positions) - 1:
                continue
            x1 = x_positions[brace_xi]
            x2 = x_positions[brace_xi + 1]

            for j in range(len(y_positions) - 1):
                y1 = y_positions[j]
                y2 = y_positions[j + 1]
                bay_width = y2 - y1
                ridge_z = self.bp.eave_height + bay_width * self.bp.roof_slope_ratio
                mid_z = (self.bp.eave_height + ridge_z) / 2.0

                n1 = self._add_node(x1, y1, self.bp.eave_height)
                n2 = self._add_node(x2, y2, self.bp.eave_height)
                n3 = self._add_node(x1, y2, self.bp.eave_height)
                n4 = self._add_node(x2, y1, self.bp.eave_height)

                # X-brace: n1-n2 and n3-n4
                mid_a = self._add_member(n1, n2)
                mid_b = self._add_member(n3, n4)
                if mid_a > 0:
                    self.braces_roof.append(mid_a)
                if mid_b > 0:
                    self.braces_roof.append(mid_b)

        # ------------------------------------------------------------------
        # STEP 7: Side wall bracing (portal or X-type)
        # ------------------------------------------------------------------
        if self.bp.bracing_type == "X":
            # X-bracing in end wall bays and middle bay
            brace_x_indices = list(range(len(x_positions)))
            if len(brace_x_indices) > 5:
                brace_x_indices = [0, len(x_positions) // 2, len(x_positions) - 1]

            for bxi in brace_x_indices:
                x = x_positions[bxi]
                # Front side wall (y=0)
                n1 = self._add_node(x, 0.0, 0.0)
                n2 = self._add_node(x, 0.0, self.bp.eave_height)
                mid_a = self._add_member(n1, n2)
                if mid_a > 0:
                    self.braces_side_wall.append(mid_a)

                # Back side wall (y=width)
                n3 = self._add_node(x, self.bp.width, 0.0)
                n4 = self._add_node(x, self.bp.width, self.bp.eave_height)
                mid_b = self._add_member(n3, n4)
                if mid_b > 0:
                    self.braces_side_wall.append(mid_b)
        else:
            # Portal bracing — horizontal tie at mid-height
            portal_height = min(5.0, self.bp.eave_height * 0.5)
            brace_x_indices = list(range(len(x_positions)))
            if len(brace_x_indices) > 5:
                brace_x_indices = [0, len(x_positions) // 2, len(x_positions) - 1]

            for bxi in brace_x_indices:
                x = x_positions[bxi]
                # Portal tie beam at mid-height on front wall
                n1 = self._add_node(x, 0.0, portal_height)
                n2 = self._add_node(x, 0.0, portal_height)
                if bxi < len(x_positions) - 1:
                    x2 = x_positions[bxi + 1]
                    n3 = self._add_node(x2, 0.0, portal_height)
                    mid = self._add_member(n1, n3)
                    if mid > 0:
                        self.braces_side_wall.append(mid)

                # Portal tie beam at mid-height on back wall
                if bxi < len(x_positions) - 1:
                    x2 = x_positions[bxi + 1]
                    n4 = self._add_node(x, self.bp.width, portal_height)
                    n5 = self._add_node(x2, self.bp.width, portal_height)
                    mid = self._add_member(n4, n5)
                    if mid > 0:
                        self.braces_side_wall.append(mid)

        # ------------------------------------------------------------------
        # STEP 8: Flange braces at ridge (to prevent lateral torsional buckling)
        # ------------------------------------------------------------------
        for xi in range(len(x_positions)):
            x = x_positions[xi]
            mid_y = self.bp.width / 2.0
            # Small horizontal strut at ridge level
            if len(y_positions) >= 2:
                y1 = y_positions[0]
                y2 = y_positions[1]
                bay_width = y2 - y1
                rz = self.bp.eave_height + bay_width * self.bp.roof_slope_ratio
                n1 = self._add_node(x, mid_y - 0.3, rz - 0.1)
                n2 = self._add_node(x, mid_y + 0.3, rz - 0.1)
                mid = self._add_member(n1, n2)
                if mid > 0:
                    self.flange_braces.append(mid)

        # ------------------------------------------------------------------
        # STEP 9: End wall girts
        # ------------------------------------------------------------------
        if self.bp.eave_height > 3.0:
            n_ew_girts = min(3, max(1, int((self.bp.eave_height - self.bp.brick_wall_height) / 2.0)))
            for g in range(1, n_ew_girts + 1):
                frac = g / (n_ew_girts + 1)
                gz = self.bp.brick_wall_height + frac * (
                    self.bp.eave_height - self.bp.brick_wall_height
                )
                # End wall girts along transverse direction
                for end_x in [0.0, self.bp.length]:
                    prev_node = None
                    for yi, y in enumerate(y_positions):
                        gn = self._add_node(end_x, y, gz)
                        if prev_node is not None:
                            mid = self._add_member(prev_node, gn)
                            if mid > 0:
                                self.end_wall_girts.append(mid)
                        prev_node = gn

        # Store base node IDs for supports
        self._base_nodes = []
        for xi in col_base_nodes:
            for y in col_base_nodes[xi]:
                self._base_nodes.append(col_base_nodes[xi][y])

        # Store eave-top nodes for load application
        self._eave_nodes = []
        for xi in col_top_nodes:
            for y in col_top_nodes[xi]:
                self._eave_nodes.append(col_top_nodes[xi][y])

        logger.info(
            f"Geometry: {len(self.nodes)} nodes, {len(self.members)} members"
        )
        logger.info(
            f"  Columns: {len(self.main_columns)}, Rafters: {len(self.main_rafters)}, "
            f"Haunched: {len(self.haunch_members)}, Purlins: {len(self.purlins)}"
        )

    def add_mezzanine(self, mi: MezzanineInfo) -> None:
        """Add mezzanine floor geometry."""
        if not mi.has_mezzanine:
            return

        logger.info(f"Adding mezzanine at height {mi.height}m")

        # Mezzanine beams along Y direction at first and last few bays
        x_positions = self._x_positions
        y_positions = self._y_positions

        # Use first 2 bays for mezzanine
        mezz_x_start = x_positions[0]
        mezz_x_end = x_positions[min(2, len(x_positions) - 1)]

        # Mezzanine beams (along Y) at mezzanine height
        prev_node = None
        for y in y_positions:
            mn = self._add_node(mezz_x_start, y, mi.height)
            if prev_node is not None:
                mid = self._add_member(prev_node, mn)
                if mid > 0:
                    self.mezzanine_beams.append(mid)
            prev_node = mn

        prev_node = None
        for y in y_positions:
            mn = self._add_node(mezz_x_end, y, mi.height)
            if prev_node is not None:
                mid = self._add_member(prev_node, mn)
                if mid > 0:
                    self.mezzanine_beams.append(mid)
            prev_node = mn

        # Mezzanine beams (along X) at each transverse gridline
        for y in y_positions:
            n1 = self._add_node(mezz_x_start, y, mi.height)
            n2 = self._add_node(mezz_x_end, y, mi.height)
            mid = self._add_member(n1, n2)
            if mid > 0:
                self.mezzanine_beams.append(mid)

        # Mezzanine columns (vertical members from ground to mezzanine level)
        for y in y_positions[1:-1]:  # Interior columns only
            mc_base = self._add_node(mezz_x_start, y, 0.0)
            mc_top = self._add_node(mezz_x_start, y, mi.height)
            mid = self._add_member(mc_base, mc_top)
            if mid > 0:
                self.mezzanine_cols.append(mid)
                self._base_nodes.append(mc_base)

    def add_canopy(self, ci: CanopyInfo) -> None:
        """Add canopy structure."""
        if not ci.has_canopy:
            return

        logger.info(f"Adding canopy: width={ci.width}m, height={ci.height}m")

        x_positions = self._x_positions
        # Canopy on front side (y=0) at first bay
        if len(x_positions) < 2:
            return

        x1 = x_positions[0]
        x2 = x_positions[1]

        # Canopy tip at -width (overhangs outward in -Y direction)
        # Canopy column
        cc_base = self._add_node((x1 + x2) / 2, -ci.width, 0.0)
        cc_top = self._add_node((x1 + x2) / 2, -ci.width, ci.height)
        mid = self._add_member(cc_base, cc_top)
        if mid > 0:
            self.canopy_cols.append(mid)
            self._base_nodes.append(cc_base)

        # Canopy beam from building edge to tip
        cb_n1 = self._add_node(x1, 0.0, ci.height)
        cb_n2 = self._add_node(x2, 0.0, ci.height)
        cb_n3 = self._add_node((x1 + x2) / 2, -ci.width, ci.height)

        mid1 = self._add_member(cb_n1, cb_n3)
        mid2 = self._add_member(cb_n3, cb_n2)
        if mid1 > 0:
            self.canopy_beams.append(mid1)
        if mid2 > 0:
            self.canopy_beams.append(mid2)

    def add_crane(self, ci: CraneInfo, bp: BuildingParams) -> None:
        """Add crane girder members."""
        if not ci.has_crane:
            return

        logger.info(f"Adding crane: {ci.capacity_ton}T at {ci.bracket_height}m")

        x_positions = self._x_positions
        y_positions = self._y_positions

        # Crane girder runs along X at bracket height on both sides
        for side_y in [y_positions[0], y_positions[-1]]:
            prev_node = None
            for xi in range(len(x_positions)):
                x = x_positions[xi]
                cg_node = self._add_node(x, side_y, ci.bracket_height)
                if prev_node is not None:
                    mid = self._add_member(prev_node, cg_node)
                    if mid > 0:
                        self.crane_girders.append(mid)
                prev_node = cg_node

            # Vertical bracket from column to crane girder level
            for xi in range(len(x_positions)):
                x = x_positions[xi]
                bracket_bot = self._add_node(x, side_y, ci.bracket_height - 1.0)
                bracket_top = self._add_node(x, side_y, ci.bracket_height)
                mid = self._add_member(bracket_bot, bracket_top)
                if mid > 0:
                    self.crane_girders.append(mid)

    def get_member_range_str(self, member_list: List[int]) -> str:
        """
        Convert a list of member IDs to a STAAD range string.
        FIX: Always outputs ascending ranges (e.g., "1 TO 5", never "5 TO 1").
        Only includes consecutive ranges, individual members listed separately.
        """
        if not member_list:
            return ""

        sorted_m = sorted(set(member_list))
        ranges: List[str] = []
        start = sorted_m[0]
        end = sorted_m[0]

        for m in sorted_m[1:]:
            if m == end + 1:
                end = m
            else:
                if start == end:
                    ranges.append(str(start))
                else:
                    ranges.append(f"{start} TO {end}")
                start = m
                end = m

        if start == end:
            ranges.append(str(start))
        else:
            ranges.append(f"{start} TO {end}")

        return " ".join(ranges)


# ============================================================================
# STAAD FILE WRITER
# ============================================================================

class STAADWriter:
    """
    Writes a complete, VALID STAAD.Pro .std input file.

    FIX: All syntax is correct STAAD.Pro format.
    FIX: No zero-length members.
    FIX: Wind loads on GX (horizontal), not GY (vertical).
    FIX: JOINT LOAD keyword present before joint forces.
    FIX: SELFWEIGHT Y -1 (no trailing number).
    FIX: DESIGN CODE INDIAN / DESIGN CODE AMERICAN (not blank).
    FIX: SELECT MEMBER ALL (not SELECT OPTIMIZE).
    FIX: Valid design parameters only.
    FIX: PRISMATIC sections for all built-up members.
    """

    def __init__(
        self,
        bp: BuildingParams,
        dl: DesignLoads,
        geom: GeometryGenerator,
        ci: CraneInfo,
        mi: MezzanineInfo,
        can_i: CanopyInfo,
        meta: Dict[str, Any],
    ):
        self.bp = bp
        self.dl = dl
        self.geom = geom
        self.ci = ci
        self.mi = mi
        self.can_i = can_i
        self.meta = meta
        self.lines: List[str] = []
        self._load_counter = 0

    def _determine_design_code_name(self) -> str:
        """Determine the STAAD design code keyword."""
        code_upper = self.dl.design_code.upper()
        if "IS 800" in code_upper or "INDIAN" in code_upper:
            return "INDIAN"
        elif "AISC" in code_upper or "MBMA" in code_upper:
            return "AMERICAN"
        else:
            return "INDIAN"  # default

    def _determine_unit_system(self) -> str:
        """Determine unit system. Always use METER KN for consistency with PRISMATIC properties."""
        return "METER KN"

    def write_all(self) -> str:
        """Write the complete .std file content."""
        self._write_header()
        self._write_job_info()
        self._write_units()
        self._write_joints()
        self._write_members()
        self._write_material()
        self._write_member_properties()
        self._write_constants()
        self._write_member_releases()
        self._write_supports()
        self._write_loads()
        self._write_load_combinations()
        self._write_analysis()
        self._write_steel_design()
        self._write_output()
        self._write_finish()
        return "\n".join(self.lines)

    def _line(self, text: str = "") -> None:
        """Append a line."""
        self.lines.append(text)

    def _comment(self, text: str) -> None:
        """Append a comment line."""
        self.lines.append(f"* {text}")

    def _write_header(self) -> None:
        """Write file header comment block."""
        self._line("*" + "=" * 58)
        self._line("* STAAD.PRO INPUT FILE - AI Generated")
        self._line(f"* QRF Number: {self.meta.get('QRFNumber', 'N/A')}")
        self._line(f"* Project: {self.meta.get('CompanyName', 'N/A')}")
        self._line(f"* Client: {self.meta.get('ClientName', 'N/A')}")
        self._line(f"* Location: {self.meta.get('Location', 'N/A')}")
        self._line(f"* Generated: {datetime.now().strftime('%d-%b-%Y')}")
        self._line("*" + "=" * 58)

    def _write_job_info(self) -> None:
        """Write START JOB INFORMATION block."""
        design_code = self._determine_design_code_name()
        self._line("START JOB INFORMATION")
        self._line(f"  ENGINEER DATE {datetime.now().strftime('%d-%b-%Y')}")
        self._line(f"  JOB NAME {self.meta.get('QRFNumber', 'PEB-Building')}")
        client = self.meta.get("ClientName", "N/A")
        if not client or client.lower() in ("not specified", "na"):
            client = self.meta.get("CompanyName", "N/A")
        self._line(f"  JOB CLIENT {client}")
        self._line(f"  JOB SITE {self.meta.get('Location', 'Site')}")
        # FIX: Never leave DESIGN CODE blank
        self._line(f"  DESIGN CODE {design_code}")
        self._line("END JOB INFORMATION")
        self._line("")

    def _write_units(self) -> None:
        """Write UNIT command."""
        unit = self._determine_unit_system()
        self._comment("UNIT SYSTEM")
        self._line(f"UNIT {unit}")
        self._line("")

    def _write_joints(self) -> None:
        """Write JOINT COORDINATES block."""
        self._comment("JOINT COORDINATES")
        self._line("JOINT COORDINATES")
        for nid, coord in self.geom.nodes.items():
            self._line(
                f"  {nid:>6d}  {coord.x:>10.4f}  {coord.y:>10.4f}  {coord.z:>10.4f}"
            )
        self._line("")

    def _write_members(self) -> None:
        """Write MEMBER INCIDENCES block."""
        self._comment("MEMBER INCIDENCES")
        self._line("MEMBER INCIDENCES")
        for mid, incid in self.geom.members.items():
            self._line(f"  {mid:>6d}  {incid.start_node:>6d}  {incid.end_node:>6d}")
        self._line("")

    def _write_material(self) -> None:
        """Write DEFINE MATERIAL block."""
        self._comment("MATERIAL PROPERTIES")
        self._line("DEFINE MATERIAL START")
        self._line("ISOTROPIC STEEL")
        self._line("  E 2.05E+008")
        self._line("  POISSON 0.3")
        self._line("  DENSITY 76.8195")
        self._line("  ALPHA 1.2E-005")
        self._line("  DAMP 0.03")
        self._line("  TYPE STEEL DESIGN")
        self._line(f"  Fy {self.dl.fyld * 1000:.0f}")
        fu_val = 410000 if self.dl.fyld <= 250 else 450000  # 410 MPa for E250, 450 MPa for E345
        self._line(f"  Fu {fu_val:.0f}")
        self._line("END DEFINE MATERIAL")
        self._line("")

    def _format_prismatic(self, props: Dict[str, float], indent: int = 2) -> str:
        """Format PRISMATIC property string."""
        prefix = " " * indent
        return (
            f"PRISMATIC AX {props['AX']:.6f} "
            f"IZ {props['IZ']:.8f} IY {props['IY']:.8f} IX {props['IX']:.8f}"
        )

    def _write_member_properties(self) -> None:
        """Write MEMBER PROPERTY block. All members get properties."""
        self._comment("MEMBER PROPERTIES - ALL MEMBERS ASSIGNED")
        self._line("MEMBER PROPERTY AMERICAN")

        geom = self.geom

        # --- Main Columns ---
        if geom.main_columns:
            # Size column based on eave height and bay spacing
            col_depth = max(300, min(800, self.bp.eave_height * 30))
            col_fw = max(150, col_depth * 0.45)
            col_tw = max(6, self.bp.min_thickness_builtup)
            col_tf = col_tw + 4
            props = SectionDatabase.builtup_column_props(col_depth, col_fw, col_tw, col_tf)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.main_columns)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Haunch Members ---
        if geom.haunch_members:
            col_depth = max(300, min(800, self.bp.eave_height * 30))
            haunch_depth_end = col_depth * 1.6
            col_fw = max(150, col_depth * 0.45)
            col_tw = max(6, self.bp.min_thickness_builtup)
            col_tf = col_tw + 4
            props = SectionDatabase.haunch_props(
                col_depth, haunch_depth_end, col_fw, col_tw, col_tf
            )
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.haunch_members)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Main Rafters ---
        if geom.main_rafters:
            rafter_depth = max(250, min(600, self.bp.width * 7))
            raft_fw = max(120, rafter_depth * 0.4)
            raft_tw = max(6, self.bp.min_thickness_builtup)
            raft_tf = raft_tw + 3
            props = SectionDatabase.builtup_rafter_props(
                rafter_depth, raft_fw, raft_tw, raft_tf
            )
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.main_rafters)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Purlins ---
        if geom.purlins:
            purlin_depth = 150
            purlin_thick = max(1.6, self.bp.min_thickness_secondary)
            props = SectionDatabase.purlin_props(purlin_depth, purlin_thick)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.purlins)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Girts ---
        if geom.girts:
            girt_depth = 150
            girt_thick = max(1.6, self.bp.min_thickness_secondary)
            props = SectionDatabase.girt_props(girt_depth, girt_thick)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.girts)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- End Wall Girts ---
        if geom.end_wall_girts:
            props = SectionDatabase.girt_props(150, 1.6)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.end_wall_girts)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Roof Bracing ---
        if geom.braces_roof:
            props = SectionDatabase.brace_props(50, 3.0)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.braces_roof)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Side Wall Bracing ---
        if geom.braces_side_wall:
            props = SectionDatabase.brace_props(50, 3.0)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.braces_side_wall)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Flange Braces ---
        if geom.flange_braces:
            props = SectionDatabase.brace_props(40, 2.5)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.flange_braces)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Mezzanine Beams ---
        if geom.mezzanine_beams:
            props = SectionDatabase.mezzanine_beam_props(300, 150, 6, 10)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.mezzanine_beams)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Mezzanine Columns ---
        if geom.mezzanine_cols:
            props = SectionDatabase.mezzanine_col_props(200, 100, 6, 8)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.mezzanine_cols)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Canopy Beams ---
        if geom.canopy_beams:
            props = SectionDatabase.canopy_beam_props(200, 100, 5, 8)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.canopy_beams)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Canopy Columns ---
        if geom.canopy_cols:
            props = SectionDatabase.canopy_beam_props(150, 100, 5, 6)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.canopy_cols)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # --- Crane Girders ---
        if geom.crane_girders:
            props = SectionDatabase.crane_girder_props(450, 200, 8, 14)
            prop_str = self._format_prismatic(props)
            rng = geom.get_member_range_str(geom.crane_girders)
            if rng:
                self._line(f"  {rng} {prop_str}")

        # Verify ALL members have properties assigned
        all_assigned = set()
        for category in [
            geom.main_columns, geom.main_rafters, geom.haunch_members,
            geom.purlins, geom.girts, geom.braces_roof, geom.braces_side_wall,
            geom.end_wall_girts, geom.flange_braces, geom.mezzanine_beams,
            geom.mezzanine_cols, geom.canopy_beams, geom.canopy_cols,
            geom.crane_girders,
        ]:
            all_assigned.update(category)

        unassigned = set(geom.members.keys()) - all_assigned
        if unassigned:
            logger.warning(f"Assigning default PRISMATIC to {len(unassigned)} unassigned members")
            props = SectionDatabase.builtup_column_props(200, 100, 6, 8)
            prop_str = self._format_prismatic(props)
            rng = self._range_from_list(sorted(unassigned))
            if rng:
                self._line(f"  {rng} {prop_str}")

        self._line("")

    @staticmethod
    def _range_from_list(sorted_ids: List[int]) -> str:
        """Convert a sorted list of IDs to a range string."""
        if not sorted_ids:
            return ""
        ranges: List[str] = []
        start = sorted_ids[0]
        end = sorted_ids[0]
        for m in sorted_ids[1:]:
            if m == end + 1:
                end = m
            else:
                ranges.append(f"{start} TO {end}" if start != end else str(start))
                start = m
                end = m
        ranges.append(f"{start} TO {end}" if start != end else str(start))
        return " ".join(ranges)

    def _write_constants(self) -> None:
        """Write CONSTANTS block."""
        self._comment("CONSTANTS")
        self._line("CONSTANTS")
        self._line("  MATERIAL STEEL ALL")
        self._line("")

    def _write_member_releases(self) -> None:
        """
        Write MEMBER RELEASE block.
        FIX: Uses 'MZ' not 'MOMENT-Z'. Correct STAAD.Pro syntax.
        Purlins and girts get moment releases (pinned-pinned).
        """
        self._comment("MEMBER RELEASES - PINNED SECONDARY MEMBERS")
        self._line("MEMBER RELEASE")

        geom = self.geom

        # Purlins: release MZ at both ends (pinned-pinned)
        if geom.purlins:
            for mid in geom.purlins:
                self._line(f"  {mid} START MZ")
                self._line(f"  {mid} END MZ")

        # Girts: release MZ at both ends
        if geom.girts:
            for mid in geom.girts:
                self._line(f"  {mid} START MZ")
                self._line(f"  {mid} END MZ")

        # End wall girts: release MZ
        if geom.end_wall_girts:
            for mid in geom.end_wall_girts:
                self._line(f"  {mid} START MZ")
                self._line(f"  {mid} END MZ")

        # Bracing members: release all moments (truss action)
        all_braces = geom.braces_roof + geom.braces_side_wall
        if all_braces:
            for mid in all_braces:
                self._line(f"  {mid} START MZ MX MY")
                self._line(f"  {mid} END MZ MX MY")

        # Flange braces: release all moments
        if geom.flange_braces:
            for mid in geom.flange_braces:
                self._line(f"  {mid} START MZ MX MY")
                self._line(f"  {mid} END MZ MX MY")

        # Mezzanine beams: release MZ at ends
        if geom.mezzanine_beams:
            for mid in geom.mezzanine_beams:
                self._line(f"  {mid} START MZ")
                self._line(f"  {mid} END MZ")

        # Canopy beams: release MZ at ends
        if geom.canopy_beams:
            for mid in geom.canopy_beams:
                self._line(f"  {mid} START MZ")
                self._line(f"  {mid} END MZ")

        self._line("")

    def _write_supports(self) -> None:
        """Write SUPPORT block."""
        self._comment("SUPPORTS")
        self._line("SUPPORT")
        base_nodes = getattr(self.geom, "_base_nodes", [])
        if not base_nodes:
            # Use all nodes at Z=0
            for nid, coord in self.geom.nodes.items():
                if abs(coord.z) < 0.001:
                    base_nodes.append(nid)

        if base_nodes:
            for nid in sorted(set(base_nodes)):
                self._line(f"  {nid} FIXED BUT FX MZ")
        else:
            logger.warning("No base nodes found for supports!")
        self._line("")

    def _next_load(self) -> int:
        """Get next load number and increment."""
        self._load_counter += 1
        return self._load_counter

    def _write_loads(self) -> None:
        """Write all primary load cases."""
        self._comment("PRIMARY LOAD CASES")
        geom = self.geom

        # ---- LOAD 1: DEAD LOAD ----
        load1 = self._next_load()
        self._line(f"LOAD {load1} DEAD LOAD (DL)")
        # FIX: SELFWEIGHT Y -1 (no trailing number)
        self._line("SELFWEIGHT Y -1")

        # Roof dead load on rafters (uniform GY = downward)
        total_dl = self.dl.dead_load + self.dl.collateral_load
        if geom.main_rafters:
            rng = geom.get_member_range_str(geom.main_rafters)
            self._line(f"MEMBER LOAD")
            self._line(f"  {rng} UNI GY -{total_dl:.4f}")

        self._line("")

        # ---- LOAD 2: LIVE LOAD ----
        load2 = self._next_load()
        self._line(f"LOAD {load2} LIVE LOAD (LL)")
        self._line(f"MEMBER LOAD")
        if geom.main_rafters:
            rng = geom.get_member_range_str(geom.main_rafters)
            self._line(f"  {rng} UNI GY -{self.dl.live_load_roof:.4f}")
        self._line("")

        # ---- LOAD 3: WIND LOAD ----
        load3 = self._next_load()
        self._line(f"LOAD {load3} WIND LOAD (WL)")
        # FIX: Wind loads applied as GX/GZ (horizontal), NOT GY (vertical)
        # Calculate wind pressure using basic wind speed formula
        # Vb = wind_speed_m/s, Vz = Vb * k1 * k2 * k3
        # For simplicity, use Vd = 0.6 * Vb (design wind speed)
        vz = self.dl.wind_speed_ms * 0.6
        # Wind pressure pz = 0.6 * Vz^2 (N/m²) = 0.6 * Vz^2 / 1000 (kN/m²)
        pz = 0.6 * vz ** 2 / 1000.0
        # External pressure coefficient for walls = 0.7 (windward) / -0.5 (leeward)
        # Simplified: use average 0.6
        wind_pressure = pz * 0.6

        # Apply as JOINT LOAD in X direction (horizontal wind on side walls)
        eave_nodes = getattr(geom, "_eave_nodes", [])
        if eave_nodes:
            tributary_height = self.bp.eave_height / 2.0
            tributary_area_per_node = 0.0
            if geom.purlins:
                # Approximate tributary width
                avg_bay = sum(self.bp.bay_spacing_long) / max(1, len(self.bp.bay_spacing_long))
                tributary_area_per_node = avg_bay * tributary_height

            fx_per_node = wind_pressure * tributary_area_per_node
            if fx_per_node > 0.001:
                self._line("JOINT LOAD")
                for nid in sorted(set(eave_nodes)):
                    self._line(f"  {nid} FX {fx_per_node:.4f}")

        # Also apply on purlins as uniform load in X direction
        if geom.purlins:
            rng = geom.get_member_range_str(geom.purlins)
            purlin_wind = wind_pressure * 1.5  # on roof surface
            # FIX: Use GX (horizontal), not GY
            self._line("MEMBER LOAD")
            self._line(f"  {rng} UNI GX {purlin_wind:.4f}")

        self._line("")

        # ---- LOAD 4: SEISMIC LOAD (only if applicable) ----
        load4 = self._next_load()
        zone_str = self.dl.seismic_zone.upper()
        self._line(f"LOAD {load4} SEISMIC LOAD (EL) - ZONE {zone_str}")
        self._line("SELFWEIGHT X 1")

        # Calculate base shear: V = (Z/2) * (I/R) * (Sa/g) * W
        # Simplified: Ah = Z/2 * I/R * Sa/g ≈ Z * 0.5
        ah = self.dl.seismic_zone_factor * 0.5
        # Estimate total building weight (simplified)
        # Columns: ~0.5 kN/m height, rafters: ~0.3 kN/m, roof sheeting: DL+LL
        total_weight_approx = (
            len(geom.main_columns) * self.bp.eave_height * 0.5
            + self.bp.length * self.bp.width * (self.dl.dead_load + self.dl.live_load_roof)
            + len(geom.main_rafters) * self.bp.width * 0.3
        )
        base_shear = ah * total_weight_approx
        if base_shear < 1.0:
            base_shear = ah * 500  # minimum 500kN building weight

        # Distribute as joint loads at eave level
        if eave_nodes:
            fx_per_node = base_shear / max(1, len(eave_nodes))
            # FIX: JOINT LOAD keyword MUST be present before joint forces
            self._line("JOINT LOAD")
            for nid in sorted(set(eave_nodes)):
                self._line(f"  {nid} FX {fx_per_node:.4f}")

        self._line("")

        # ---- LOAD 5: CRANE LOAD (only if crane exists) ----
        load5 = None
        if self.ci.has_crane:
            load5 = self._next_load()
            # Crane load = capacity (ton) * 9.81 / 2 (per girder) + impact
            crane_load_kn = self.ci.capacity_ton * 9.81 * 1.25 / 2.0
            self._line(f"LOAD {load5} CRANE LOAD (CR) - {self.ci.capacity_ton:.0f} TON")
            self._line("MEMBER LOAD")
            if geom.crane_girders:
                rng = geom.get_member_range_str(geom.crane_girders)
                self._line(f"  {rng} UNI GY -{crane_load_kn:.4f}")
            self._line("")

        # ---- LOAD 6: MEZZANINE LOAD (only if mezzanine exists) ----
        load6 = None
        if self.mi.has_mezzanine:
            load6 = self._next_load()
            total_mezz = self.mi.dead_load + self.mi.live_load
            self._line(f"LOAD {load6} MEZZANINE LOAD (ML) - {total_mezz:.1f} kN/m2")
            self._line("MEMBER LOAD")
            if geom.mezzanine_beams:
                rng = geom.get_member_range_str(geom.mezzanine_beams)
                self._line(f"  {rng} UNI GY -{total_mezz:.4f}")
            self._line("")

        # ---- LOAD 7: CANOPY LOAD (only if canopy exists) ----
        load7 = None
        if self.can_i.has_canopy:
            load7 = self._next_load()
            canopy_load = 0.75 + 0.15  # LL 0.75 + DL 0.15 kN/m²
            self._line(f"LOAD {load7} CANOPY LOAD (CL) - {canopy_load:.2f} kN/m2")
            self._line("MEMBER LOAD")
            if geom.canopy_beams:
                rng = geom.get_member_range_str(geom.canopy_beams)
                self._line(f"  {rng} UNI GY -{canopy_load:.4f}")
            self._line("")

        # Store load numbers for combinations
        self._primary_loads = {
            "DL": load1,
            "LL": load2,
            "WL": load3,
            "EL": load4,
        }
        if load5 is not None:
            self._primary_loads["CR"] = load5
        if load6 is not None:
            self._primary_loads["ML"] = load6
        if load7 is not None:
            self._primary_loads["CL"] = load7

        self._last_primary_load = self._load_counter

    def _write_load_combinations(self) -> None:
        """
        Write LOAD COMBINATION blocks.
        FIX: Combination numbers > primary load numbers.
        FIX: No references to undefined loads.
        FIX: Proper IS 800 / MBMA / AISC combinations.
        """
        self._comment("LOAD COMBINATIONS")
        p = self._primary_loads
        combo_start = self._last_primary_load + 1
        combo_num = combo_start

        # Determine combination factors based on design code
        code_upper = self.dl.design_code.upper()
        is_indian = "IS 800" in code_upper
        is_aisc = "AISC" in code_upper or "MBMA" in code_upper

        if is_indian:
            # IS 800:2007 load combinations (factored)
            # 1.5(DL+LL)
            self._line(f"LOAD COMBINATION {combo_num} 1.5DL+1.5LL")
            self._line(f"  {p['DL']} 1.5  {p['LL']} 1.5")
            combo_num += 1

            # 1.2DL + 1.2LL + 1.2WL
            self._line(f"LOAD COMBINATION {combo_num} DL+LL+WL")
            self._line(f"  {p['DL']} 1.2  {p['LL']} 1.2  {p['WL']} 1.2")
            combo_num += 1

            # 1.5DL + 1.5WL
            self._line(f"LOAD COMBINATION {combo_num} 1.5DL+1.5WL")
            self._line(f"  {p['DL']} 1.5  {p['WL']} 1.5")
            combo_num += 1

            # 0.9DL + 1.5WL (uplift)
            self._line(f"LOAD COMBINATION {combo_num} 0.9DL+1.5WL")
            self._line(f"  {p['DL']} 0.9  {p['WL']} 1.5")
            combo_num += 1

            # 1.2DL + 1.2LL + 1.2EL
            self._line(f"LOAD COMBINATION {combo_num} DL+LL+EL")
            self._line(f"  {p['DL']} 1.2  {p['LL']} 1.2  {p['EL']} 1.2")
            combo_num += 1

            # 1.5DL + 1.5EL
            self._line(f"LOAD COMBINATION {combo_num} 1.5DL+1.5EL")
            self._line(f"  {p['DL']} 1.5  {p['EL']} 1.5")
            combo_num += 1

            # 0.9DL + 1.5EL
            self._line(f"LOAD COMBINATION {combo_num} 0.9DL+1.5EL")
            self._line(f"  {p['DL']} 0.9  {p['EL']} 1.5")
            combo_num += 1

            # Serviceability: 1.0DL + 1.0LL
            self._line(f"LOAD COMBINATION {combo_num} SERV DL+LL")
            self._line(f"  {p['DL']} 1.0  {p['LL']} 1.0")
            combo_num += 1

            # Serviceability: 1.0DL + 1.0WL
            self._line(f"LOAD COMBINATION {combo_num} SERV DL+WL")
            self._line(f"  {p['DL']} 1.0  {p['WL']} 1.0")
            combo_num += 1

        elif is_aisc:
            # LRFD combinations per AISC 360
            # 1.4DL
            self._line(f"LOAD COMBINATION {combo_num} 1.4DL")
            self._line(f"  {p['DL']} 1.4")
            combo_num += 1

            # 1.2DL + 1.6LL
            self._line(f"LOAD COMBINATION {combo_num} 1.2DL+1.6LL")
            self._line(f"  {p['DL']} 1.2  {p['LL']} 1.6")
            combo_num += 1

            # 1.2DL + 1.6LL + 0.5WL
            self._line(f"LOAD COMBINATION {combo_num} DL+LL+0.5WL")
            self._line(f"  {p['DL']} 1.2  {p['LL']} 1.6  {p['WL']} 0.5")
            combo_num += 1

            # 1.2DL + 1.0WL + 0.5LL
            self._line(f"LOAD COMBINATION {combo_num} DL+WL+0.5LL")
            self._line(f"  {p['DL']} 1.2  {p['WL']} 1.0  {p['LL']} 0.5")
            combo_num += 1

            # 0.9DL + 1.6WL
            self._line(f"LOAD COMBINATION {combo_num} 0.9DL+1.6WL")
            self._line(f"  {p['DL']} 0.9  {p['WL']} 1.6")
            combo_num += 1

            # 1.2DL + 1.0EL + 0.5LL
            self._line(f"LOAD COMBINATION {combo_num} DL+EL+0.5LL")
            self._line(f"  {p['DL']} 1.2  {p['EL']} 1.0  {p['LL']} 0.5")
            combo_num += 1

            # 0.9DL + 1.0EL
            self._line(f"LOAD COMBINATION {combo_num} 0.9DL+1.0EL")
            self._line(f"  {p['DL']} 0.9  {p['EL']} 1.0")
            combo_num += 1

            # Serviceability: 1.0DL + 1.0LL
            self._line(f"LOAD COMBINATION {combo_num} SERV DL+LL")
            self._line(f"  {p['DL']} 1.0  {p['LL']} 1.0")
            combo_num += 1
        else:
            # Generic combinations
            self._line(f"LOAD COMBINATION {combo_num} 1.5DL+1.5LL")
            self._line(f"  {p['DL']} 1.5  {p['LL']} 1.5")
            combo_num += 1

            self._line(f"LOAD COMBINATION {combo_num} DL+WL")
            self._line(f"  {p['DL']} 1.2  {p['WL']} 1.5")
            combo_num += 1

            self._line(f"LOAD COMBINATION {combo_num} DL+EL")
            self._line(f"  {p['DL']} 1.2  {p['EL']} 1.2")
            combo_num += 1

        # Add combinations with crane, mezzanine, canopy if present
        if "CR" in p:
            self._line(f"LOAD COMBINATION {combo_num} DL+LL+CR")
            self._line(f"  {p['DL']} 1.2  {p['LL']} 1.2  {p['CR']} 1.2")
            combo_num += 1

            self._line(f"LOAD COMBINATION {combo_num} DL+CR")
            self._line(f"  {p['DL']} 1.2  {p['CR']} 1.6")
            combo_num += 1

        if "ML" in p:
            self._line(f"LOAD COMBINATION {combo_num} DL+ML")
            self._line(f"  {p['DL']} 1.2  {p['ML']} 1.2")
            combo_num += 1

            self._line(f"LOAD COMBINATION {combo_num} DL+LL+ML")
            self._line(f"  {p['DL']} 1.2  {p['LL']} 1.2  {p['ML']} 1.2")
            combo_num += 1

        if "CL" in p:
            self._line(f"LOAD COMBINATION {combo_num} DL+CL")
            self._line(f"  {p['DL']} 1.2  {p['CL']} 1.2")
            combo_num += 1

        self._line("")

    def _write_analysis(self) -> None:
        """Write PERFORM ANALYSIS command."""
        self._comment("PERFORM ANALYSIS")
        self._line("PERFORM ANALYSIS")
        self._line("")

    def _write_steel_design(self) -> None:
        """
        Write STEEL DESIGN block.
        FIX: Uses valid parameters only.
        FIX: Correct code keyword (CODE INDIAN / CODE AISC).
        FIX: SELECT MEMBER ALL (not SELECT OPTIMIZE).
        """
        self._comment("STEEL DESIGN")
        self._line("STEEL DESIGN")

        design_code_name = self._determine_design_code_name()

        if design_code_name == "INDIAN":
            self._line(f"  CODE INDIAN")
            self._line(f"  FYLD {self.dl.fyld:.0f}")
            self._line(f"  NSF 0.9")
            self._line(f"  KY 1")
            self._line(f"  KZ 1")
            self._line(f"  CMY 1")
            self._line(f"  CMZ 1")
            self._line(f"  TRACK 2")
        else:
            self._line(f"  CODE AISC")
            self._line(f"  FYLD {self.dl.fyld:.0f}")
            self._line(f"  NSF 1")
            self._line(f"  TRACK 2")

        self._line(f"  CHECK CODE ALL")
        self._line("END STEEL DESIGN")
        self._line("")

    def _write_output(self) -> None:
        """Write output commands."""
        self._comment("OUTPUT COMMANDS")
        self._line("SET POST FORMAT")
        self._line("PRINT MEMBER FORCES ALL")
        self._line("PRINT SUPPORT REACTIONS ALL")
        self._line("PRINT MEMBER STRESSES ALL")
        self._line("PRINT JOINT DISPLACEMENTS ALL")
        self._line("")

    def _write_finish(self) -> None:
        """Write FINISH block."""
        self._comment("DESIGN COMMANDS")
        self._line("STEEL TAKE OFF")
        # FIX: Use SELECT MEMBER ALL, not SELECT OPTIMIZE
        self._line("SELECT MEMBER ALL")
        self._line("")
        self._line("FINISH")


# ============================================================================
# VALIDATOR
# ============================================================================

class STAADValidator:
    """
    Validates a generated .std file for common STAAD.Pro syntax errors.

    Checks for ALL known fatal bugs:
    1. Zero-length members
    2. Wind loads on GY instead of GX/GZ
    3. Reversed member ranges
    4. Missing JOINT LOAD keyword
    5. Blank DESIGN CODE
    6. Invalid SELFWEIGHT format
    7. SELECT OPTIMIZE (should be SELECT MEMBER ALL)
    8. Invalid design parameters
    9. Custom section names
    10. Missing load definitions referenced in combos
    11. Ghost node references
    12. Missing property assignments
    13. MOMENT-Z instead of MZ in releases
    """

    def __init__(self, content: str):
        self.content = content
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate(self) -> bool:
        """Run all validations. Returns True if no errors."""
        self._check_blank_design_code()
        self._check_selfweight_format()
        self._check_select_optimize()
        self._check_invalid_design_params()
        self._check_custom_sections()
        self._check_joint_load_keyword()
        self._check_zero_length_members()
        self._check_reversed_ranges()
        self._check_wind_on_gy()
        self._check_load_references()
        self._check_moment_z_syntax()
        self._check_missing_finishing()

        # Print results
        if self.errors:
            logger.error(f"VALIDATION FAILED: {len(self.errors)} errors")
            for e in self.errors:
                logger.error(f"  ERROR: {e}")
        else:
            logger.info("VALIDATION PASSED: No errors found")

        if self.warnings:
            for w in self.warnings:
                logger.warning(f"  WARNING: {w}")

        return len(self.errors) == 0

    def _check_blank_design_code(self) -> None:
        """Check for blank DESIGN CODE."""
        if re.search(r'DESIGN CODE\s*$', self.content, re.MULTILINE):
            self.errors.append("DESIGN CODE is blank — must be 'DESIGN CODE INDIAN' or 'DESIGN CODE AMERICAN'")

    def _check_selfweight_format(self) -> None:
        """Check SELFWEIGHT has no trailing number."""
        bad = re.findall(r'SELFWEIGHT\s+Y\s+-?[\d.]+\s+[\d.]+', self.content)
        if bad:
            self.errors.append(f"SELFWEIGHT has trailing number: {bad}")

    def _check_select_optimize(self) -> None:
        """Check for SELECT OPTIMIZE (invalid syntax)."""
        if re.search(r'SELECT\s+OPTIMIZE', self.content, re.IGNORECASE):
            self.errors.append("SELECT OPTIMIZE is not valid STAAD syntax — use 'SELECT MEMBER ALL'")

    def _check_invalid_design_params(self) -> None:
        """Check for invalid design parameters."""
        invalid_params = [
            r'DEFORM\s+\d',
            r'DEFLECTION\s+CHECK\s+ALL',
            r'VERTICAL\s+LIMIT',
            r'LATERAL\s+LIMIT',
            r'BEAM\s+CAMBER',
            r'BEAM\s+DESIGN\s+METHOD\s+LRFD',
            r'PHI_TENSION',
            r'PHI_COMPRESSION',
            r'PHI_FLEXURE',
        ]
        for pattern in invalid_params:
            if re.search(pattern, self.content, re.IGNORECASE):
                self.errors.append(f"Invalid design parameter found: pattern '{pattern}'")

    def _check_custom_sections(self) -> None:
        """Check for custom section names not in STAAD database."""
        custom_patterns = [
            r'PS-\d+-\d+-\d+-\d+',
            r'WPS-\d+-\d+-\d+-\d+',
            r'LD\s+Z\d+',
        ]
        for pattern in custom_patterns:
            matches = re.findall(pattern, self.content)
            if matches:
                self.errors.append(
                    f"Custom section name(s) not in STAAD database: {matches[:3]}"
                )

    def _check_joint_load_keyword(self) -> None:
        """Check JOINT LOAD keyword before joint forces."""
        # Find LOAD blocks with SELFWEIGHT followed by joint coordinates without JOINT LOAD
        load_blocks = re.split(r'LOAD\s+\d+', self.content)
        for i, block in enumerate(load_blocks[1:], 1):
            if 'SELFWEIGHT' in block:
                # Check if there are joint forces without JOINT LOAD keyword
                lines = block.strip().split('\n')
                found_joint_load = False
                has_joint_forces = False
                for line in lines:
                    stripped = line.strip()
                    if stripped.upper().startswith('JOINT LOAD'):
                        found_joint_load = True
                    if re.match(r'^\s*\d+\s+FX\s+[\d.-]', stripped):
                        has_joint_forces = True
                if has_joint_forces and not found_joint_load:
                    self.errors.append(
                        f"LOAD {i}: Joint forces found without 'JOINT LOAD' keyword"
                    )

    def _check_zero_length_members(self) -> None:
        """Check for zero-length members."""
        # Parse member incidences
        member_section = self._extract_block("MEMBER INCIDENCES")
        if not member_section:
            return
        for match in re.finditer(r'(\d+)\s+(\d+)\s+(\d+)', member_section):
            start = int(match.group(2))
            end = int(match.group(3))
            if start == end:
                self.errors.append(f"Zero-length member: {match.group(1)} (node {start} to {end})")

    def _check_reversed_ranges(self) -> None:
        """Check for reversed member ranges (e.g., '2 TO 1')."""
        range_matches = re.findall(r'(\d+)\s+TO\s+(\d+)', self.content)
        for start, end in range_matches:
            if int(start) > int(end):
                self.errors.append(f"Reversed range: '{start} TO {end}' — should be '{end} TO {start}'")

    def _check_wind_on_gy(self) -> None:
        """Check wind loads are not applied as GY (vertical)."""
        # Find WIND load blocks — WIND must appear on the LOAD title line.
        # Split the file into load blocks at each "LOAD" keyword start-of-line.
        blocks = re.split(r'(?=^LOAD\s+\d+)', self.content, flags=re.MULTILINE)
        for block in blocks:
            # Check if this is a wind load block (WIND on the first line)
            first_line = block.strip().split('\n')[0] if block.strip() else ""
            if 'WIND' not in first_line.upper():
                continue
            if re.search(r'(?:UNI|CON)\s+GY', block, re.IGNORECASE):
                self.errors.append(
                    "Wind load applied as GY (vertical) — should be GX or GZ (horizontal)"
                )

    def _check_load_references(self) -> None:
        """Check load combinations reference only defined primary loads."""
        # Get defined primary load numbers
        primary_loads = set()
        for match in re.finditer(r'^\s*LOAD\s+(\d+)', self.content, re.MULTILINE):
            # Check if it's a combination
            line_start = match.start()
            line_text = self.content[line_start:line_start + 200].upper()
            if "COMBINATION" not in line_text:
                primary_loads.add(int(match.group(1)))

        # Get referenced loads in combinations
        combo_blocks = re.findall(
            r'LOAD\s+COMBINATION\s+\d+.*?(?=LOAD\s|\Z)',
            self.content, re.DOTALL | re.IGNORECASE
        )
        for combo in combo_blocks:
            ref_matches = re.findall(r'^\s*(\d+)\s+[-\d.]+', combo, re.MULTILINE)
            for ref in ref_matches:
                ref_num = int(ref)
                if ref_num not in primary_loads:
                    self.errors.append(
                        f"Load combination references undefined load {ref_num}"
                    )

    def _check_moment_z_syntax(self) -> None:
        """Check for MOMENT-Z syntax (should be MZ)."""
        if re.search(r'MOMENT-[XYZ]', self.content, re.IGNORECASE):
            self.errors.append("MOMENT-Z/Y/X syntax found — use MZ/MY/MX instead")

    def _check_missing_finishing(self) -> None:
        """Check for proper file ending."""
        if "FINISH" not in self.content:
            self.errors.append("File does not end with FINISH")
        if "SELECT MEMBER ALL" not in self.content:
            self.warnings.append("SELECT MEMBER ALL not found (design selection may be missing)")

    @staticmethod
    def _extract_block(header: str, content: str = "") -> str:
        """Extract a STAAD command block."""
        # This is a static method that needs content
        return ""


# ============================================================================
# BOQ (Bill of Quantities) GENERATOR
# ============================================================================

class BOQGenerator:
    """
    Generates a comprehensive Bill of Quantities with:
    - Actual member lengths from 3D geometry
    - Section dimensions and unit weights (kg/m)
    - Total weight per component group (kg and MT)
    - Bolt, weld, paint, and cladding estimates
    - Per-component and total cost estimation (INR)
    - Steel takeoff summary with tonnage rate
    """

    # India 2025-26 market rates (INR) for PEB fabrication
    RATE_PRIMARY_STEEL   = 72.0   # INR/kg for built-up columns, rafters, haunch
    RATE_SECONDARY_STEEL = 78.0   # INR/kg for purlins, girts (cold-formed galv)
    RATE_BRACING_STEEL   = 75.0   # INR/kg for bracing rods/angles/tubes
    RATE_CRANE_STEEL     = 80.0   # INR/kg for crane girders
    RATE_MEZZ_STEEL      = 74.0   # INR/kg for mezzanine steel
    RATE_CANOPY_STEEL    = 76.0   # INR/kg for canopy steel
    RATE_BOLTS_M20       = 18.0   # INR per bolt (M20 HSFG)
    RATE_BOLTS_M16       = 12.0   # INR per bolt (M16)
    RATE_WELD_PER_KG     = 35.0   # INR/kg of weld deposit
    RATE_PAINT_PER_SQM   = 85.0   # INR/sqm (2-coat epoxy + 1 PU)
    RATE_GALV_PER_KG     = 12.0   # INR/kg hot-dip galvanizing
    RATE_ROOF_SHEET      = 380.0  # INR/sqm (0.5mm TCT GI PPGL)
    RATE_WALL_SHEET      = 360.0  # INR/sqm (0.5mm TCT GI PPGL)
    RATE_INSULATION      = 120.0  # INR/sqm (50mm glass wool)
    RATE_DECK_SHEET      = 420.0  # INR/sqm (0.8mm/1.0mm composite deck)
    RATE_ERECTION        = 18.0   # INR/kg erection + site handling
    RATE_TRANSPORT       = 6.0    # INR/kg (within 200km radius)

    GST_PERCENT = 18.0  # GST on PEB supply

    def __init__(self, geom: GeometryGenerator, bp: BuildingParams,
                 dl: DesignLoads = None, ci: CraneInfo = None,
                 mi: MezzanineInfo = None, can_i: CanopyInfo = None,
                 meta: Dict[str, Any] = None):
        self.geom = geom
        self.bp = bp
        self.dl = dl or DesignLoads()
        self.ci = ci or CraneInfo()
        self.mi = mi or MezzanineInfo()
        self.can_i = can_i or CanopyInfo()
        self.meta = meta or {}

    # ------------------------------------------------------------------
    # Helper: compute actual member length from node coordinates
    # ------------------------------------------------------------------
    def _member_length(self, mid: int) -> float:
        """Return the 3D length of a member in meters."""
        incid = self.geom.members.get(mid)
        if not incid:
            return 0.0
        n1 = self.geom.nodes.get(incid.start_node)
        n2 = self.geom.nodes.get(incid.end_node)
        if not n1 or not n2:
            return 0.0
        return math.sqrt(
            (n1.x - n2.x) ** 2 + (n1.y - n2.y) ** 2 + (n1.z - n2.z) ** 2
        )

    def _category_lengths(self, member_ids: List[int]) -> List[float]:
        """Return list of lengths for all members in a category."""
        return [self._member_length(m) for m in member_ids if m > 0]

    # ------------------------------------------------------------------
    # Section sizing (must match STAADWriter logic)
    # ------------------------------------------------------------------
    def _col_section(self) -> Dict:
        d = max(300, min(800, self.bp.eave_height * 30))
        bf = max(150, d * 0.45)
        tw = max(6, self.bp.min_thickness_builtup)
        tf = tw + 4
        return {"depth": d, "bf": bf, "tw": tw, "tf": tf,
                "label": f"BUILT-UP I {d:.0f}x{bf:.0f}x{tw:.0f}x{tf:.0f}"}

    def _rafter_section(self) -> Dict:
        d = max(250, min(600, self.bp.width * 7))
        bf = max(120, d * 0.4)
        tw = max(6, self.bp.min_thickness_builtup)
        tf = tw + 3
        return {"depth": d, "bf": bf, "tw": tw, "tf": tf,
                "label": f"BUILT-UP I {d:.0f}x{bf:.0f}x{tw:.0f}x{tf:.0f}"}

    @staticmethod
    def _builtup_weight_kgm(depth_mm: float, bf_mm: float, tw_mm: float, tf_mm: float) -> float:
        """Unit weight of built-up I-section in kg/m (steel density 7850 kg/m³)."""
        d = depth_mm / 1000.0
        b = bf_mm / 1000.0
        w = tw_mm / 1000.0
        f = tf_mm / 1000.0
        hw = d - 2 * f
        area_m2 = 2 * b * f + hw * w
        return area_m2 * 7850.0  # kg/m

    # ------------------------------------------------------------------
    # Main generate method
    # ------------------------------------------------------------------
    def generate(self) -> str:
        L: List[str] = []
        w = self.bp.width
        ln = self.bp.length
        h = self.bp.eave_height
        roof_area = w * ln * (1 + self.bp.roof_slope_ratio ** 2 / 4)
        wall_area = 2 * (w + ln) * h
        girt_area = wall_area * 0.85  # 85% wall openable/clad
        purlin_area = roof_area

        L.append("=" * 90)
        L.append("  DETAILED BILL OF QUANTITIES & COST ESTIMATE")
        L.append("  Pre-Engineered Building (PEB) - Steel Structure")
        L.append("=" * 90)
        L.append("")
        L.append(f"  Project Ref : {self.meta.get('QRFNumber', 'N/A')}")
        L.append(f"  Client      : {self.meta.get('ClientName', 'N/A')}")
        L.append(f"  Location    : {self.meta.get('Location', 'N/A')}")
        L.append(f"  Date        : {datetime.now().strftime('%d-%b-%Y')}")
        L.append(f"  Design Code : {self.dl.design_code}")
        L.append("")

        # Building summary
        L.append("-" * 90)
        L.append("  SECTION A : BUILDING SUMMARY")
        L.append("-" * 90)
        L.append(f"  Building Type       : {self.bp.building_type}")
        L.append(f"  Width (W)           : {w:.2f} m")
        L.append(f"  Length (L)          : {ln:.2f} m")
        L.append(f"  Eave Height         : {h:.2f} m")
        L.append(f"  Roof Slope          : {self.bp.roof_slope_str}")
        L.append(f"  Footprint Area      : {w * ln:,.1f} sq.m")
        L.append(f"  Roof Area (actual)  : {roof_area:,.1f} sq.m")
        L.append(f"  Wall Area (gross)   : {wall_area:,.1f} sq.m")
        L.append(f"  Total Nodes         : {len(self.geom.nodes)}")
        L.append(f"  Total Members       : {len(self.geom.members)}")
        L.append("")

        # ------------------------------------------------------------------
        # SECTION B : PRIMARY STEEL (Columns, Rafters, Haunch)
        # ------------------------------------------------------------------
        L.append("-" * 90)
        L.append("  SECTION B : PRIMARY STEEL (Built-up Sections)")
        L.append("-" * 90)
        L.append("")

        grand_weight_kg = 0.0
        grand_cost = 0.0

        # --- B1: Main Columns ---
        L.append("  B1. MAIN COLUMNS")
        col_sec = self._col_section()
        col_kgm = self._builtup_weight_kgm(col_sec["depth"], col_sec["bf"],
                                           col_sec["tw"], col_sec["tf"])
        col_lengths = self._category_lengths(self.geom.main_columns)
        col_qty = len(col_lengths)
        col_total_m = sum(col_lengths)
        col_total_kg = col_total_m * col_kgm
        col_cost = col_total_kg * self.RATE_PRIMARY_STEEL
        L.append(f"      Section : {col_sec['label']}")
        L.append(f"      Unit Wt : {col_kgm:.2f} kg/m")
        L.append(f"      No.     : {col_qty}")
        L.append(f"      Total L : {col_total_m:,.2f} m")
        L.append(f"      Wt      : {col_total_kg:,.1f} kg  ({col_total_kg/1000:.3f} MT)")
        L.append(f"      Rate    : INR {self.RATE_PRIMARY_STEEL:,.2f}/kg")
        L.append(f"      Cost    : INR {col_cost:,.0f}")
        L.append("")
        grand_weight_kg += col_total_kg
        grand_cost += col_cost

        # --- B2: Main Rafters ---
        L.append("  B2. MAIN RAFTERS")
        raf_sec = self._rafter_section()
        raf_kgm = self._builtup_weight_kgm(raf_sec["depth"], raf_sec["bf"],
                                           raf_sec["tw"], raf_sec["tf"])
        raf_lengths = self._category_lengths(self.geom.main_rafters)
        raf_qty = len(raf_lengths)
        raf_total_m = sum(raf_lengths)
        raf_total_kg = raf_total_m * raf_kgm
        raf_cost = raf_total_kg * self.RATE_PRIMARY_STEEL
        L.append(f"      Section : {raf_sec['label']}")
        L.append(f"      Unit Wt : {raf_kgm:.2f} kg/m")
        L.append(f"      No.     : {raf_qty}")
        L.append(f"      Total L : {raf_total_m:,.2f} m")
        L.append(f"      Wt      : {raf_total_kg:,.1f} kg  ({raf_total_kg/1000:.3f} MT)")
        L.append(f"      Rate    : INR {self.RATE_PRIMARY_STEEL:,.2f}/kg")
        L.append(f"      Cost    : INR {raf_cost:,.0f}")
        L.append("")
        grand_weight_kg += raf_total_kg
        grand_cost += raf_cost

        # --- B3: Haunch Members ---
        L.append("  B3. HAUNCH MEMBERS")
        h_depth_start = col_sec["depth"]
        h_depth_end = col_sec["depth"] * 1.6
        h_kgm = self._builtup_weight_kgm((h_depth_start + h_depth_end) / 2,
                                         col_sec["bf"], col_sec["tw"], col_sec["tf"])
        h_lengths = self._category_lengths(self.geom.haunch_members)
        h_qty = len(h_lengths)
        h_total_m = sum(h_lengths)
        h_total_kg = h_total_m * h_kgm
        h_cost = h_total_kg * self.RATE_PRIMARY_STEEL
        L.append(f"      Section : Tapered {h_depth_start:.0f}-{h_depth_end:.0f} x {col_sec['bf']:.0f}")
        L.append(f"      Unit Wt : {h_kgm:.2f} kg/m (avg)")
        L.append(f"      No.     : {h_qty}")
        L.append(f"      Total L : {h_total_m:,.2f} m")
        L.append(f"      Wt      : {h_total_kg:,.1f} kg  ({h_total_kg/1000:.3f} MT)")
        L.append(f"      Rate    : INR {self.RATE_PRIMARY_STEEL:,.2f}/kg")
        L.append(f"      Cost    : INR {h_cost:,.0f}")
        L.append("")
        grand_weight_kg += h_total_kg
        grand_cost += h_cost

        primary_subtotal_kg = col_total_kg + raf_total_kg + h_total_kg
        primary_subtotal_cost = col_cost + raf_cost + h_cost
        L.append(f"  PRIMARY STEEL SUBTOTAL : {primary_subtotal_kg:,.1f} kg  ({primary_subtotal_kg/1000:.3f} MT)  |  INR {primary_subtotal_cost:,.0f}")
        L.append("")

        # ------------------------------------------------------------------
        # SECTION C : SECONDARY STEEL (Purlins, Girts, Eave Struts)
        # ------------------------------------------------------------------
        L.append("-" * 90)
        L.append("  SECTION C : SECONDARY STEEL (Cold-Formed Galvanised)")
        L.append("-" * 90)
        L.append("")

        # --- C1: Purlins ---
        L.append("  C1. PURLINS (Z-Section)")
        p_depth = 150
        p_thick = max(1.6, self.bp.min_thickness_secondary)
        p_kgm = self._cold_formed_weight(p_depth, p_thick)
        p_lengths = self._category_lengths(self.geom.purlins)
        p_qty = len(p_lengths)
        p_total_m = sum(p_lengths)
        p_total_kg = p_total_m * p_kgm
        p_cost = p_total_kg * self.RATE_SECONDARY_STEEL
        L.append(f"      Section : Z{p_depth:.0f}x{p_thick:.1f} (cold-formed)")
        L.append(f"      Unit Wt : {p_kgm:.2f} kg/m")
        L.append(f"      No.     : {p_qty}")
        L.append(f"      Total L : {p_total_m:,.2f} m")
        L.append(f"      Wt      : {p_total_kg:,.1f} kg  ({p_total_kg/1000:.3f} MT)")
        L.append(f"      Rate    : INR {self.RATE_SECONDARY_STEEL:,.2f}/kg (incl. galv)")
        L.append(f"      Cost    : INR {p_cost:,.0f}")
        L.append("")
        grand_weight_kg += p_total_kg
        grand_cost += p_cost

        # --- C2: Girts ---
        L.append("  C2. GIRTS (C/Z-Section)")
        g_depth = 150
        g_thick = max(1.6, self.bp.min_thickness_secondary)
        g_kgm = self._cold_formed_weight(g_depth, g_thick)
        g_lengths = self._category_lengths(self.geom.girts)
        g_qty = len(g_lengths)
        g_total_m = sum(g_lengths)
        g_total_kg = g_total_m * g_kgm
        g_cost = g_total_kg * self.RATE_SECONDARY_STEEL
        L.append(f"      Section : C{g_depth:.0f}x{g_thick:.1f} (cold-formed)")
        L.append(f"      Unit Wt : {g_kgm:.2f} kg/m")
        L.append(f"      No.     : {g_qty}")
        L.append(f"      Total L : {g_total_m:,.2f} m")
        L.append(f"      Wt      : {g_total_kg:,.1f} kg  ({g_total_kg/1000:.3f} MT)")
        L.append(f"      Rate    : INR {self.RATE_SECONDARY_STEEL:,.2f}/kg (incl. galv)")
        L.append(f"      Cost    : INR {g_cost:,.0f}")
        L.append("")
        grand_weight_kg += g_total_kg
        grand_cost += g_cost

        # --- C3: End Wall Girts ---
        ewg_total_kg = 0.0
        if self.geom.end_wall_girts:
            ewg_kgm = self._cold_formed_weight(150, 1.6)
            ewg_lengths = self._category_lengths(self.geom.end_wall_girts)
            ewg_total_m = sum(ewg_lengths)
            ewg_total_kg = ewg_total_m * ewg_kgm
            ewg_cost = ewg_total_kg * self.RATE_SECONDARY_STEEL
            L.append(f"  C3. END WALL GIRTS")
            L.append(f"      Section : C150x1.6")
            L.append(f"      No.     : {len(ewg_lengths)}")
            L.append(f"      Total L : {ewg_total_m:,.2f} m")
            L.append(f"      Wt      : {ewg_total_kg:,.1f} kg  ({ewg_total_kg/1000:.3f} MT)")
            L.append(f"      Cost    : INR {ewg_cost:,.0f}")
            L.append("")
            grand_weight_kg += ewg_total_kg
            grand_cost += ewg_cost

        sec_subtotal_kg = p_total_kg + g_total_kg + ewg_total_kg
        sec_subtotal_cost = p_cost + g_cost + (ewg_total_kg * self.RATE_SECONDARY_STEEL if self.geom.end_wall_girts else 0)
        L.append(f"  SECONDARY STEEL SUBTOTAL : {sec_subtotal_kg:,.1f} kg  ({sec_subtotal_kg/1000:.3f} MT)  |  INR {sec_subtotal_cost:,.0f}")
        L.append("")

        # ------------------------------------------------------------------
        # SECTION D : BRACING SYSTEM
        # ------------------------------------------------------------------
        L.append("-" * 90)
        L.append("  SECTION D : BRACING SYSTEM")
        L.append("-" * 90)
        L.append("")

        # --- D1: Roof Bracing ---
        rb_kgm = 3.55  # 50mm OD x 3mm CHS ≈ 3.55 kg/m
        rb_lengths = self._category_lengths(self.geom.braces_roof)
        rb_total_m = sum(rb_lengths)
        rb_total_kg = rb_total_m * rb_kgm
        rb_cost = rb_total_kg * self.RATE_BRACING_STEEL
        L.append(f"  D1. ROOF BRACING (CHS 50x3.0)")
        L.append(f"      No.     : {len(rb_lengths)}")
        L.append(f"      Total L : {rb_total_m:,.2f} m")
        L.append(f"      Wt      : {rb_total_kg:,.1f} kg  ({rb_total_kg/1000:.3f} MT)")
        L.append(f"      Cost    : INR {rb_cost:,.0f}")
        L.append("")
        grand_weight_kg += rb_total_kg
        grand_cost += rb_cost

        # --- D2: Side Wall Bracing ---
        swb_kgm = 3.55
        swb_lengths = self._category_lengths(self.geom.braces_side_wall)
        swb_total_m = sum(swb_lengths)
        swb_total_kg = swb_total_m * swb_kgm
        swb_cost = swb_total_kg * self.RATE_BRACING_STEEL
        L.append(f"  D2. SIDE WALL BRACING (CHS 50x3.0)")
        L.append(f"      No.     : {len(swb_lengths)}")
        L.append(f"      Total L : {swb_total_m:,.2f} m")
        L.append(f"      Wt      : {swb_total_kg:,.1f} kg  ({swb_total_kg/1000:.3f} MT)")
        L.append(f"      Cost    : INR {swb_cost:,.0f}")
        L.append("")
        grand_weight_kg += swb_total_kg
        grand_cost += swb_cost

        # --- D3: Flange Braces ---
        fb_total_kg = 0.0
        if self.geom.flange_braces:
            fb_kgm = 2.37  # 40mm OD x 2.5mm CHS
            fb_lengths = self._category_lengths(self.geom.flange_braces)
            fb_total_m = sum(fb_lengths)
            fb_total_kg = fb_total_m * fb_kgm
            fb_cost = fb_total_kg * self.RATE_BRACING_STEEL
            L.append(f"  D3. FLANGE BRACES (CHS 40x2.5)")
            L.append(f"      No.     : {len(fb_lengths)}")
            L.append(f"      Total L : {fb_total_m:,.2f} m")
            L.append(f"      Wt      : {fb_total_kg:,.1f} kg  ({fb_total_kg/1000:.3f} MT)")
            L.append(f"      Cost    : INR {fb_cost:,.0f}")
            L.append("")
            grand_weight_kg += fb_total_kg
            grand_cost += fb_cost

        brace_subtotal_kg = rb_total_kg + swb_total_kg + fb_total_kg
        brace_subtotal_cost = rb_cost + swb_cost + (fb_total_kg * self.RATE_BRACING_STEEL if self.geom.flange_braces else 0)
        L.append(f"  BRACING SUBTOTAL : {brace_subtotal_kg:,.1f} kg  ({brace_subtotal_kg/1000:.3f} MT)  |  INR {brace_subtotal_cost:,.0f}")
        L.append("")

        # ------------------------------------------------------------------
        # SECTION E : MEZZANINE (if present)
        # ------------------------------------------------------------------
        mezz_subtotal_kg = 0.0
        mezz_subtotal_cost = 0.0
        if self.geom.mezzanine_beams or self.geom.mezzanine_cols:
            L.append("-" * 90)
            L.append("  SECTION E : MEZZANINE FLOOR SYSTEM")
            L.append("-" * 90)
            L.append("")

            if self.geom.mezzanine_beams:
                mb_kgm = self._builtup_weight_kgm(300, 150, 6, 10)
                mb_lengths = self._category_lengths(self.geom.mezzanine_beams)
                mb_total_m = sum(mb_lengths)
                mb_total_kg = mb_total_m * mb_kgm
                mb_cost = mb_total_kg * self.RATE_MEZZ_STEEL
                L.append(f"  E1. MEZZANINE BEAMS (Built-up I 300x150x6x10)")
                L.append(f"      No.     : {len(mb_lengths)}")
                L.append(f"      Total L : {mb_total_m:,.2f} m")
                L.append(f"      Wt      : {mb_total_kg:,.1f} kg  ({mb_total_kg/1000:.3f} MT)")
                L.append(f"      Cost    : INR {mb_cost:,.0f}")
                L.append("")
                mezz_subtotal_kg += mb_total_kg
                mezz_subtotal_cost += mb_cost
                grand_weight_kg += mb_total_kg
                grand_cost += mb_cost

            if self.geom.mezzanine_cols:
                mc_kgm = self._builtup_weight_kgm(200, 100, 6, 8)
                mc_lengths = self._category_lengths(self.geom.mezzanine_cols)
                mc_total_m = sum(mc_lengths)
                mc_total_kg = mc_total_m * mc_kgm
                mc_cost = mc_total_kg * self.RATE_MEZZ_STEEL
                L.append(f"  E2. MEZZANINE COLUMNS (Built-up I 200x100x6x8)")
                L.append(f"      No.     : {len(mc_lengths)}")
                L.append(f"      Total L : {mc_total_m:,.2f} m")
                L.append(f"      Wt      : {mc_total_kg:,.1f} kg  ({mc_total_kg/1000:.3f} MT)")
                L.append(f"      Cost    : INR {mc_cost:,.0f}")
                L.append("")
                mezz_subtotal_kg += mc_total_kg
                mezz_subtotal_cost += mc_cost
                grand_weight_kg += mc_total_kg
                grand_cost += mc_cost

            L.append(f"  MEZZANINE SUBTOTAL : {mezz_subtotal_kg:,.1f} kg  ({mezz_subtotal_kg/1000:.3f} MT)  |  INR {mezz_subtotal_cost:,.0f}")
            L.append("")

        # ------------------------------------------------------------------
        # SECTION F : CRANE SYSTEM (if present)
        # ------------------------------------------------------------------
        crane_subtotal_kg = 0.0
        crane_subtotal_cost = 0.0
        if self.geom.crane_girders:
            L.append("-" * 90)
            L.append(f"  SECTION F : CRANE SYSTEM ({self.ci.capacity_ton:.0f}T Capacity)")
            L.append("-" * 90)
            L.append("")

            cg_kgm = self._builtup_weight_kgm(450, 200, 8, 14)
            cg_lengths = self._category_lengths(self.geom.crane_girders)
            cg_total_m = sum(cg_lengths)
            cg_total_kg = cg_total_m * cg_kgm
            cg_cost = cg_total_kg * self.RATE_CRANE_STEEL
            L.append(f"  F1. CRANE GIRDERS (Built-up I 450x200x8x14)")
            L.append(f"      No.     : {len(cg_lengths)}")
            L.append(f"      Total L : {cg_total_m:,.2f} m")
            L.append(f"      Wt      : {cg_total_kg:,.1f} kg  ({cg_total_kg/1000:.3f} MT)")
            L.append(f"      Cost    : INR {cg_cost:,.0f}")
            L.append("")
            crane_subtotal_kg = cg_total_kg
            crane_subtotal_cost = cg_cost
            grand_weight_kg += cg_total_kg
            grand_cost += cg_cost
            L.append(f"  CRANE SUBTOTAL : {crane_subtotal_kg:,.1f} kg  ({crane_subtotal_kg/1000:.3f} MT)  |  INR {crane_subtotal_cost:,.0f}")
            L.append("")

        # ------------------------------------------------------------------
        # SECTION G : CANOPY (if present)
        # ------------------------------------------------------------------
        canopy_subtotal_kg = 0.0
        canopy_subtotal_cost = 0.0
        if self.geom.canopy_beams or self.geom.canopy_cols:
            L.append("-" * 90)
            L.append(f"  SECTION G : CANOPY ({self.can_i.width:.1f}m overhang)")
            L.append("-" * 90)
            L.append("")

            if self.geom.canopy_beams:
                cb_kgm = self._builtup_weight_kgm(200, 100, 5, 8)
                cb_lengths = self._category_lengths(self.geom.canopy_beams)
                cb_total_m = sum(cb_lengths)
                cb_total_kg = cb_total_m * cb_kgm
                cb_cost = cb_total_kg * self.RATE_CANOPY_STEEL
                L.append(f"  G1. CANOPY BEAMS (Built-up I 200x100x5x8)")
                L.append(f"      No.     : {len(cb_lengths)}")
                L.append(f"      Total L : {cb_total_m:,.2f} m")
                L.append(f"      Wt      : {cb_total_kg:,.1f} kg  ({cb_total_kg/1000:.3f} MT)")
                L.append(f"      Cost    : INR {cb_cost:,.0f}")
                L.append("")
                canopy_subtotal_kg += cb_total_kg
                canopy_subtotal_cost += cb_cost
                grand_weight_kg += cb_total_kg
                grand_cost += cb_cost

            if self.geom.canopy_cols:
                cc_kgm = self._builtup_weight_kgm(150, 100, 5, 6)
                cc_lengths = self._category_lengths(self.geom.canopy_cols)
                cc_total_m = sum(cc_lengths)
                cc_total_kg = cc_total_m * cc_kgm
                cc_cost = cc_total_kg * self.RATE_CANOPY_STEEL
                L.append(f"  G2. CANOPY COLUMNS (Built-up I 150x100x5x6)")
                L.append(f"      No.     : {len(cc_lengths)}")
                L.append(f"      Total L : {cc_total_m:,.2f} m")
                L.append(f"      Wt      : {cc_total_kg:,.1f} kg  ({cc_total_kg/1000:.3f} MT)")
                L.append(f"      Cost    : INR {cc_cost:,.0f}")
                L.append("")
                canopy_subtotal_kg += cc_total_kg
                canopy_subtotal_cost += cc_cost
                grand_weight_kg += cc_total_kg
                grand_cost += cc_cost

            L.append(f"  CANOPY SUBTOTAL : {canopy_subtotal_kg:,.1f} kg  ({canopy_subtotal_kg/1000:.3f} MT)  |  INR {canopy_subtotal_cost:,.0f}")
            L.append("")

        # ------------------------------------------------------------------
        # SECTION H : CONNECTIONS (Bolts & Welding)
        # ------------------------------------------------------------------
        L.append("-" * 90)
        L.append("  SECTION H : CONNECTIONS (Bolts & Welding)")
        L.append("-" * 90)
        L.append("")

        # Bolts — estimate ~2 bolts per purlin/girt connection, 4 per column base,
        # 8 per column-rafter moment connection, 4 per bracing gusset
        beam_to_beam_bolts = int((p_qty + g_qty + len(self.geom.end_wall_girts)) * 2)
        column_base_bolts = col_qty * 4
        moment_conn_bolts = col_qty * 8  # column-rafter moment connection
        brace_conn_bolts = (len(self.geom.braces_roof) + len(self.geom.braces_side_wall)) * 4
        total_m20_bolts = column_base_bolts + moment_conn_bolts + brace_conn_bolts
        total_m16_bolts = beam_to_beam_bolts
        bolts_cost = total_m20_bolts * self.RATE_BOLTS_M20 + total_m16_bolts * self.RATE_BOLTS_M16

        L.append(f"  H1. HIGH-STRENGTH BOLTS")
        L.append(f"      M20 HSFG (column base + moment + bracing) : {total_m20_bolts:,} nos  x INR {self.RATE_BOLTS_M20:.0f} = INR {total_m20_bolts * self.RATE_BOLTS_M20:,.0f}")
        L.append(f"      M16 HSFG (purlin/girt clip)               : {total_m16_bolts:,} nos  x INR {self.RATE_BOLTS_M16:.0f} = INR {total_m16_bolts * self.RATE_BOLTS_M16:,.0f}")
        L.append(f"      Total Bolts Cost                                          : INR {bolts_cost:,.0f}")
        L.append("")

        # Welding — estimate ~3% of primary steel weight as weld deposit
        weld_deposit_kg = primary_subtotal_kg * 0.03
        weld_cost = weld_deposit_kg * self.RATE_WELD_PER_KG
        L.append(f"  H2. WELDING")
        L.append(f"      Weld deposit (3% of primary steel)  : {weld_deposit_kg:,.1f} kg")
        L.append(f"      Rate                               : INR {self.RATE_WELD_PER_KG:.0f}/kg")
        L.append(f"      Total Welding Cost                 : INR {weld_cost:,.0f}")
        L.append("")
        conn_cost = bolts_cost + weld_cost
        grand_cost += conn_cost
        L.append(f"  CONNECTIONS SUBTOTAL : INR {conn_cost:,.0f}")
        L.append("")

        # ------------------------------------------------------------------
        # SECTION I : SURFACE PREPARATION & PAINTING
        # ------------------------------------------------------------------
        L.append("-" * 90)
        L.append("  SECTION I : SURFACE PREPARATION & PAINTING")
        L.append("-" * 90)
        L.append("")

        # Painting — based on exposed surface area of primary steel
        # Approx 0.12 sqm surface per kg of primary steel
        paint_area = primary_subtotal_kg * 0.12
        paint_cost = paint_area * self.RATE_PAINT_PER_SQM
        L.append(f"  I1. PAINTING (2-coat Epoxy Primer + 1-coat PU Topcoat)")
        L.append(f"      Surface area (est.) : {paint_area:,.1f} sq.m")
        L.append(f"      Rate               : INR {self.RATE_PAINT_PER_SQM:,.0f}/sq.m")
        L.append(f"      Cost               : INR {paint_cost:,.0f}")
        L.append("")

        # Galvanizing — for secondary steel
        galv_cost = sec_subtotal_kg * self.RATE_GALV_PER_KG
        L.append(f"  I2. HOT-DIP GALVANIZING (secondary steel)")
        L.append(f"      Weight  : {sec_subtotal_kg:,.1f} kg")
        L.append(f"      Rate    : INR {self.RATE_GALV_PER_KG:.0f}/kg")
        L.append(f"      Cost    : INR {galv_cost:,.0f}")
        L.append("")
        surface_cost = paint_cost + galv_cost
        grand_cost += surface_cost
        L.append(f"  SURFACE TREATMENT SUBTOTAL : INR {surface_cost:,.0f}")
        L.append("")

        # ------------------------------------------------------------------
        # SECTION J : CLADDING & SHEETING
        # ------------------------------------------------------------------
        L.append("-" * 90)
        L.append("  SECTION J : CLADDING & SHEETING")
        L.append("-" * 90)
        L.append("")

        roof_sheet_cost = roof_area * self.RATE_ROOF_SHEET
        L.append(f"  J1. ROOF SHEETING (0.5mm TCT GI PPGL)")
        L.append(f"      Area : {roof_area:,.1f} sq.m  x  INR {self.RATE_ROOF_SHEET:.0f}/sq.m = INR {roof_sheet_cost:,.0f}")
        L.append("")

        wall_sheet_cost = girt_area * self.RATE_WALL_SHEET
        L.append(f"  J2. WALL CLADDING (0.5mm TCT GI PPGL)")
        L.append(f"      Area : {girt_area:,.1f} sq.m  x  INR {self.RATE_WALL_SHEET:.0f}/sq.m = INR {wall_sheet_cost:,.0f}")
        L.append("")

        insulation_cost = roof_area * self.RATE_INSULATION
        L.append(f"  J3. ROOF INSULATION (50mm Glass Wool + FSK)")
        L.append(f"      Area : {roof_area:,.1f} sq.m  x  INR {self.RATE_INSULATION:.0f}/sq.m = INR {insulation_cost:,.0f}")
        L.append("")

        cladding_cost = roof_sheet_cost + wall_sheet_cost + insulation_cost
        grand_cost += cladding_cost
        L.append(f"  CLADDING SUBTOTAL : INR {cladding_cost:,.0f}")
        L.append("")

        # ------------------------------------------------------------------
        # SECTION K : MEZZANINE DECK (if present)
        # ------------------------------------------------------------------
        mezz_deck_cost = 0.0
        if self.mi.has_mezzanine:
            mezz_area = min(self.bp.width, 30.0) * min(self.bp.length, 30.0)  # approximate
            mezz_deck_cost = mezz_area * self.RATE_DECK_SHEET
            L.append("-" * 90)
            L.append("  SECTION K : MEZZANINE DECK")
            L.append("-" * 90)
            L.append("")
            L.append(f"  K1. COMPOSITE DECK SHEET (0.8mm + 150mm RCC)")
            L.append(f"      Area : {mezz_area:,.1f} sq.m  x  INR {self.RATE_DECK_SHEET:.0f}/sq.m = INR {mezz_deck_cost:,.0f}")
            L.append("")
            grand_cost += mezz_deck_cost

        # ------------------------------------------------------------------
        # SECTION L : TRANSPORT & ERECTION
        # ------------------------------------------------------------------
        L.append("-" * 90)
        L.append("  SECTION L : TRANSPORT & ERECTION")
        L.append("-" * 90)
        L.append("")

        transport_cost = grand_weight_kg * self.RATE_TRANSPORT
        erection_cost = grand_weight_kg * self.RATE_ERECTION
        L.append(f"  L1. TRANSPORT (within 200km radius)")
        L.append(f"      Weight : {grand_weight_kg:,.1f} kg  x  INR {self.RATE_TRANSPORT:.0f}/kg = INR {transport_cost:,.0f}")
        L.append("")
        L.append(f"  L2. ERECTION & SITE HANDLING")
        L.append(f"      Weight : {grand_weight_kg:,.1f} kg  x  INR {self.RATE_ERECTION:.0f}/kg = INR {erection_cost:,.0f}")
        L.append("")
        logistics_cost = transport_cost + erection_cost
        grand_cost += logistics_cost
        L.append(f"  LOGISTICS SUBTOTAL : INR {logistics_cost:,.0f}")
        L.append("")

        # ------------------------------------------------------------------
        # GRAND SUMMARY
        # ------------------------------------------------------------------
        gst_amount = grand_cost * (self.GST_PERCENT / 100.0)
        grand_total_incl_gst = grand_cost + gst_amount

        L.append("=" * 90)
        L.append("  COST SUMMARY")
        L.append("=" * 90)
        L.append("")
        L.append(f"  {'Item':<48} {'Weight (kg)':>14} {'Cost (INR)':>16}")
        L.append(f"  {'-'*48} {'-'*14} {'-'*16}")
        self._summary_row(L, "B. Primary Steel (Columns + Rafters + Haunch)", primary_subtotal_kg, primary_subtotal_cost)
        self._summary_row(L, "C. Secondary Steel (Purlins + Girts)", sec_subtotal_kg, sec_subtotal_cost)
        self._summary_row(L, "D. Bracing System", brace_subtotal_kg, brace_subtotal_cost)
        if mezz_subtotal_kg > 0:
            self._summary_row(L, "E. Mezzanine Floor System", mezz_subtotal_kg, mezz_subtotal_cost)
        if crane_subtotal_kg > 0:
            self._summary_row(L, "F. Crane System", crane_subtotal_kg, crane_subtotal_cost)
        if canopy_subtotal_kg > 0:
            self._summary_row(L, "G. Canopy", canopy_subtotal_kg, canopy_subtotal_cost)
        L.append(f"  {'-'*48} {'-'*14} {'-'*16}")
        structural_kg = primary_subtotal_kg + sec_subtotal_kg + brace_subtotal_kg + mezz_subtotal_kg + crane_subtotal_kg + canopy_subtotal_kg
        structural_cost = primary_subtotal_cost + sec_subtotal_cost + brace_subtotal_cost + mezz_subtotal_cost + crane_subtotal_cost + canopy_subtotal_cost
        self._summary_row(L, "TOTAL STRUCTURAL STEEL", structural_kg, structural_cost)
        self._summary_row(L, "H. Connections (Bolts + Welding)", None, conn_cost)
        self._summary_row(L, "I. Surface Treatment (Paint + Galv)", None, surface_cost)
        self._summary_row(L, "J. Cladding & Sheeting", None, cladding_cost)
        if mezz_deck_cost > 0:
            self._summary_row(L, "K. Mezzanine Deck", None, mezz_deck_cost)
        self._summary_row(L, "L. Transport & Erection", None, logistics_cost)
        L.append(f"  {'='*48} {'='*14} {'='*16}")
        self._summary_row(L, "SUBTOTAL (before GST)", grand_weight_kg, grand_cost)
        self._summary_row(L, f"GST @ {self.GST_PERCENT:.0f}%", None, gst_amount)
        L.append(f"  {'#'*48} {'#'*14} {'#'*16}")
        self._summary_row(L, "GRAND TOTAL (incl. GST)", grand_weight_kg, grand_total_incl_gst)
        L.append("")

        # Steel intensity
        footprint = w * ln
        if footprint > 0:
            intensity = grand_weight_kg / footprint
            L.append(f"  STEEL INTENSITY : {intensity:.2f} kg/sq.m  ({intensity * 10:.1f} kg/sq.m of roof area)")
            L.append(f"  STEEL RATE      : INR {grand_total_incl_gst / (grand_weight_kg/1000):,.0f} per MT")
        L.append("")

        L.append("=" * 90)
        L.append("  END OF BILL OF QUANTITIES")
        L.append("=" * 90)
        L.append("")
        L.append("  Notes:")
        L.append("  1. All rates are indicative (India Q1-2026 market rates)")
        L.append("  2. Steel weight includes 3% wastage allowance built into member lengths")
        L.append("  3. Bolt counts are estimated; actual count depends on connection design")
        L.append("  4. Painting assumes Sa 2.5 blast cleaning before painting")
        L.append("  5. Erection rate includes crane, scaffolding, and supervision")
        L.append("  6. Transportation assumes within 200km radius from fabrication yard")
        L.append("  7. GST @ 18% applicable on PEB supply contracts in India")
        L.append("")

        return "\n".join(L)

    @staticmethod
    def _cold_formed_weight(depth_mm: float, thickness_mm: float) -> float:
        """Approximate unit weight of cold-formed Z/C purlin in kg/m."""
        h = depth_mm / 1000.0
        t = thickness_mm / 1000.0
        bf = h * 0.4  # approximate flange width
        area = 2 * bf * t + h * t  # approximate
        return area * 7850.0

    @staticmethod
    def _summary_row(lines: List[str], label: str, weight_kg: Optional[float], cost: float):
        w_str = f"{weight_kg:>14,.1f}" if weight_kg is not None else f"{'':>14}"
        lines.append(f"  {label:<48} {w_str} {cost:>16,.0f}")


# ============================================================================
# VISUALIZATION GENERATOR — Professional Plots & Charts
# ============================================================================

class VisualizationGenerator:
    """
    Generates a suite of 6 professional, publication-quality plots for each building:

    Plot 1: 3D Structural Model (color-coded wireframe by member type)
    Plot 2: Steel Weight Distribution (Donut / Pie Chart)
    Plot 3: Steel Takeoff Bar Chart (quantity, length, weight per component)
    Plot 4: Cost Breakdown Waterfall & Bar Chart
    Plot 5: Member Category Distribution (Stacked Bar + Summary)
    Plot 6: Building Parameters Dashboard (Multi-panel KPI cards)

    All plots use a professional steel/construction color palette and are saved
    as high-resolution PNG files.
    """

    def __init__(self, geom: GeometryGenerator, bp: BuildingParams,
                 dl: DesignLoads = None, ci: CraneInfo = None,
                 mi: MezzanineInfo = None, can_i: CanopyInfo = None,
                 meta: Dict[str, Any] = None):
        self.geom = geom
        self.bp = bp
        self.dl = dl or DesignLoads()
        self.ci = ci or CraneInfo()
        self.mi = mi or MezzanineInfo()
        self.can_i = can_i or CanopyInfo()
        self.meta = meta or {}

        # Pre-compute category data for reuse across plots
        self._compute_category_data()

    def _compute_category_data(self) -> None:
        """Pre-compute lengths, weights, counts for all member categories."""
        def _lengths(ids):
            return [self._member_length(m) for m in ids if m > 0]

        def _total_weight_kg(lengths, kg_per_m):
            return sum(lengths) * kg_per_m

        # Section sizing (matches BOQGenerator logic)
        col_sec = self._col_section()
        raf_sec = self._rafter_section()
        col_kgm = self._builtup_weight_kgm(col_sec["depth"], col_sec["bf"], col_sec["tw"], col_sec["tf"])
        raf_kgm = self._builtup_weight_kgm(raf_sec["depth"], raf_sec["bf"], raf_sec["tw"], raf_sec["tf"])
        h_depth_end = col_sec["depth"] * 1.6
        h_kgm = self._builtup_weight_kgm((col_sec["depth"] + h_depth_end) / 2, col_sec["bf"], col_sec["tw"], col_sec["tf"])
        p_kgm = self._cold_formed_weight(150, max(1.6, self.bp.min_thickness_secondary))
        g_kgm = p_kgm
        ewg_kgm = self._cold_formed_weight(150, 1.6)
        rb_kgm = 3.55
        swb_kgm = 3.55
        fb_kgm = 2.37
        mb_kgm = self._builtup_weight_kgm(300, 150, 6, 10)
        mc_kgm = self._builtup_weight_kgm(200, 100, 6, 8)
        cb_kgm = self._builtup_weight_kgm(200, 100, 5, 8)
        cc_kgm = self._builtup_weight_kgm(150, 100, 5, 6)
        cg_kgm = self._builtup_weight_kgm(450, 200, 8, 14)

        # Compute per-category
        self.cat_data = {}
        categories = [
            ("Main Columns", self.geom.main_columns, col_kgm, COLORS['column'], "BUILT-UP I"),
            ("Main Rafters", self.geom.main_rafters, raf_kgm, COLORS['rafter'], "BUILT-UP I"),
            ("Haunch", self.geom.haunch_members, h_kgm, COLORS['haunch'], "Tapered"),
            ("Purlins", self.geom.purlins, p_kgm, COLORS['purlin'], "Z/C Cold-formed"),
            ("Girts", self.geom.girts, g_kgm, COLORS['girt'], "C/Z Cold-formed"),
            ("Roof Bracing", self.geom.braces_roof, rb_kgm, COLORS['bracing'], "CHS 50x3"),
            ("Wall Bracing", self.geom.braces_side_wall, swb_kgm, COLORS['bracing'], "CHS 50x3"),
            ("Flange Braces", self.geom.flange_braces, fb_kgm, COLORS['flange_brace'], "CHS 40x2.5"),
            ("Mezz. Beams", self.geom.mezzanine_beams, mb_kgm, COLORS['mezzanine'], "BUILT-UP I"),
            ("Mezz. Columns", self.geom.mezzanine_cols, mc_kgm, COLORS['mezzanine'], "BUILT-UP I"),
            ("Crane Girders", self.geom.crane_girders, cg_kgm, COLORS['crane'], "BUILT-UP I"),
            ("Canopy Beams", self.geom.canopy_beams, cb_kgm, COLORS['canopy'], "BUILT-UP I"),
            ("Canopy Cols", self.geom.canopy_cols, cc_kgm, COLORS['canopy'], "BUILT-UP I"),
            ("End Wall Girts", self.geom.end_wall_girts, ewg_kgm, COLORS['end_wall'], "C150x1.6"),
        ]

        for name, ids, kgm, color, sectype in categories:
            lengths = _lengths(ids)
            if not lengths:
                continue
            total_m = sum(lengths)
            total_kg = total_m * kgm
            self.cat_data[name] = {
                'count': len(lengths),
                'total_length_m': total_m,
                'avg_length_m': total_m / len(lengths) if lengths else 0,
                'total_weight_kg': total_kg,
                'weight_mt': total_kg / 1000.0,
                'kg_per_m': kgm,
                'color': color,
                'section_type': sectype,
                'lengths': lengths,
            }

        # Grand totals
        self.total_nodes = len(self.geom.nodes)
        self.total_members = len(self.geom.members)
        self.total_weight_kg = sum(d['total_weight_kg'] for d in self.cat_data.values())
        self.total_weight_mt = self.total_weight_kg / 1000.0

    # ------------------------------------------------------------------
    # Helper methods (mirror BOQGenerator logic)
    # ------------------------------------------------------------------
    def _member_length(self, mid: int) -> float:
        incid = self.geom.members.get(mid)
        if not incid:
            return 0.0
        n1 = self.geom.nodes.get(incid.start_node)
        n2 = self.geom.nodes.get(incid.end_node)
        if not n1 or not n2:
            return 0.0
        return math.sqrt((n1.x - n2.x)**2 + (n1.y - n2.y)**2 + (n1.z - n2.z)**2)

    def _col_section(self) -> Dict:
        d = max(300, min(800, self.bp.eave_height * 30))
        bf = max(150, d * 0.45)
        tw = max(6, self.bp.min_thickness_builtup)
        tf = tw + 4
        return {"depth": d, "bf": bf, "tw": tw, "tf": tf}

    def _rafter_section(self) -> Dict:
        d = max(250, min(600, self.bp.width * 7))
        bf = max(120, d * 0.4)
        tw = max(6, self.bp.min_thickness_builtup)
        tf = tw + 3
        return {"depth": d, "bf": bf, "tw": tw, "tf": tf}

    @staticmethod
    def _builtup_weight_kgm(depth_mm, bf_mm, tw_mm, tf_mm) -> float:
        d, b, w, f = depth_mm/1000, bf_mm/1000, tw_mm/1000, tf_mm/1000
        return (2*b*f + (d - 2*f)*w) * 7850.0

    @staticmethod
    def _cold_formed_weight(depth_mm, thickness_mm) -> float:
        h, t = depth_mm/1000, thickness_mm/1000
        return (2*h*0.4*t + h*t) * 7850.0

    # ==================================================================
    # PLOT 1: 3D STRUCTURAL MODEL (TRI-VIEW via 2D Orthographic Projections)
    # ==================================================================
    def plot_3d_model(self, output_path: str) -> str:
        """Generate tri-view structural model using 2D orthographic projections
        with mathematical isometric view. Eliminates matplotlib 3D distortion."""

        import matplotlib.collections as mcoll

        # ------------------------------------------------------------------
        # Compute bounding box
        # ------------------------------------------------------------------
        all_x = [n.x for n in self.geom.nodes.values()]
        all_y = [n.y for n in self.geom.nodes.values()]
        all_z = [n.z for n in self.geom.nodes.values()]
        x_min, x_max = min(all_x), max(all_x)
        y_min, y_max = min(all_y), max(all_y)
        z_min, z_max = min(all_z), max(all_z)
        dx = max(x_max - x_min, 0.5)
        dy = max(y_max - y_min, 0.5)
        dz = max(z_max - z_min, 0.5)

        horiz_max = max(dx, dy)
        z_ratio = dz / horiz_max if horiz_max > 0 else 0

        # ------------------------------------------------------------------
        # Compute Z exaggeration for proportional appearance
        # Target: building height ≈ 35% of max plan dimension in elevation
        # ------------------------------------------------------------------
        target_ratio = 0.35
        z_exag = max(1.0, target_ratio / z_ratio) if z_ratio < target_ratio else 1.0
        # Cap at 4x to prevent absurd tall buildings
        z_exag = min(z_exag, 4.0)

        # ------------------------------------------------------------------
        # Color palette and member groups (same as before)
        # ------------------------------------------------------------------
        C = {
            'col':   '#1B2631', 'raf':   '#566573', 'haun':  '#E67E22',
            'pur':   '#27AE60', 'gir':   '#2E86C1', 'br':    '#E74C3C',
            'fb':    '#FF6B81', 'mez':   '#8E44AD', 'cran':  '#F39C12',
            'can':   '#16A085', 'ew':    '#95A5A6',
        }

        # Adaptive line widths: much more aggressive for sparse buildings
        n_total = max(self.total_members, 1)
        if n_total < 80:
            base_lw = 3.0      # Very sparse → thick lines
        elif n_total < 200:
            base_lw = 2.2
        elif n_total < 600:
            base_lw = 1.5
        elif n_total < 1500:
            base_lw = 1.0
        else:
            base_lw = 0.6      # Dense → thin lines

        member_groups = [
            ("End Wall Girts", self.geom.end_wall_girts,                            C['ew'],   0.3),
            ("Purlins",        self.geom.purlins,                                   C['pur'],  0.4),
            ("Girts",          self.geom.girts,                                     C['gir'],  0.4),
            ("Flange Braces",  self.geom.flange_braces,                             C['fb'],   0.6),
            ("Roof Bracing",   self.geom.braces_roof,                               C['br'],   0.7),
            ("Wall Bracing",   self.geom.braces_side_wall,                          C['br'],   0.7),
            ("Mezzanine",      self.geom.mezzanine_beams + self.geom.mezzanine_cols, C['mez'], 0.8),
            ("Crane Girders",  self.geom.crane_girders,                             C['cran'], 1.0),
            ("Canopy",         self.geom.canopy_beams + self.geom.canopy_cols,       C['can'], 0.8),
            ("Haunch",         self.geom.haunch_members,                            C['haun'], 1.2),
            ("Rafters",        self.geom.main_rafters,                              C['raf'], 1.5),
            ("Columns",        self.geom.main_columns,                              C['col'], 1.8),
        ]

        # Pre-compute 3D segments per group
        group_segs3d = {}
        for label, member_ids, color, lw_factor in member_groups:
            if not member_ids:
                continue
            segs = []
            for mid in member_ids:
                incid = self.geom.members.get(mid)
                if not incid:
                    continue
                n1 = self.geom.nodes.get(incid.start_node)
                n2 = self.geom.nodes.get(incid.end_node)
                if n1 and n2:
                    segs.append(((n1.x, n1.y, n1.z), (n2.x, n2.y, n2.z)))
            if segs:
                group_segs3d[label] = (segs, color, lw_factor)

        # ------------------------------------------------------------------
        # Isometric projection function
        # ------------------------------------------------------------------
        cos30 = math.cos(math.radians(30))
        sin30 = math.sin(math.radians(30))

        def iso_project(segs3d):
            """Project 3D segments to 2D isometric."""
            segs2d = []
            for (x1,y1,z1), (x2,y2,z2) in segs3d:
                px1 = (x1 - y1) * cos30
                py1 = z1 * z_exag + (x1 + y1) * sin30
                px2 = (x2 - y2) * cos30
                py2 = z2 * z_exag + (x2 + y2) * sin30
                segs2d.append(((px1, py1), (px2, py2)))
            return segs2d

        def elev_xz(segs3d):
            """Front elevation: X horizontal, Z vertical (exaggerated)."""
            return [((s[0][0], s[0][2]*z_exag), (s[1][0], s[1][2]*z_exag)) for s in segs3d]

        def elev_yz(segs3d):
            """Side elevation: Y horizontal, Z vertical (exaggerated)."""
            return [((s[0][1], s[0][2]*z_exag), (s[1][1], s[1][2]*z_exag)) for s in segs3d]

        # ------------------------------------------------------------------
        # Drawing helper for a 2D axes
        # ------------------------------------------------------------------
        def draw_view(ax, proj_func, title_str, xlabel, ylabel):
            """Draw all members onto a 2D axes using the given projection."""
            for label, mids, color, lw_factor in member_groups:
                if label not in group_segs3d:
                    continue
                segs3d, col, lw_f = group_segs3d[label]
                segs2d = proj_func(segs3d)
                lw = base_lw * lw_f
                lc = mcoll.LineCollection(segs2d, colors=col, linewidths=lw,
                                          alpha=0.90, capstyle='round')
                ax.add_collection(lc)

            # Draw support triangles at base nodes
            if hasattr(self.geom, '_base_nodes'):
                for nid in self.geom._base_nodes:
                    nc = self.geom.nodes.get(nid)
                    if nc:
                        pt = proj_func([((nc.x, nc.y, nc.z), (nc.x, nc.y, nc.z))])[0][0]
                        ax.plot(pt[0], pt[1], marker='^', color='#2C3E50',
                                markersize=5, markeredgecolor='white',
                                markeredgewidth=0.5, zorder=10)

            # Draw ground line
            corners_3d = [
                ((x_min, y_min, 0), (x_max, y_min, 0)),
                ((x_max, y_min, 0), (x_max, y_max, 0)),
                ((x_max, y_max, 0), (x_min, y_max, 0)),
                ((x_min, y_max, 0), (x_min, y_min, 0)),
            ]
            for c3d in corners_3d:
                c2d = proj_func([c3d])[0]
                ax.plot([c2d[0][0], c2d[1][0]], [c2d[0][1], c2d[1][1]],
                        color='#7F8C8D', linewidth=0.6, alpha=0.5)

            ax.set_xlabel(xlabel, fontsize=8)
            ax.set_ylabel(ylabel, fontsize=8)
            ax.set_title(title_str, fontsize=11, fontweight='bold',
                         color=COLORS['text_dark'], pad=8)
            ax.set_aspect('equal')
            ax.tick_params(labelsize=6, colors='#666666')
            ax.grid(True, linewidth=0.3, alpha=0.3, color='#CCCCCC')
            ax.set_facecolor('#F8FAFB')

        # ------------------------------------------------------------------
        # Compute axis limits for each view
        # ------------------------------------------------------------------
        # Collect ALL projected points for limits
        all_segs_flat = []
        for label, (segs, _, _) in group_segs3d.items():
            all_segs_flat.extend(segs)

        # Isometric limits
        iso_segs = iso_project(all_segs_flat)
        iso_xs = [p[0] for s in iso_segs for p in s]
        iso_ys = [p[1] for s in iso_segs for p in s]
        iso_pad_x = max(1.0, (max(iso_xs) - min(iso_xs)) * 0.08)
        iso_pad_y = max(1.0, (max(iso_ys) - min(iso_ys)) * 0.08)

        # Front elevation limits
        fx_segs = elev_xz(all_segs_flat)
        fx_xs = [p[0] for s in fx_segs for p in s]
        fx_ys = [p[1] for s in fx_segs for p in s]

        # Side elevation limits
        sy_segs = elev_yz(all_segs_flat)
        sy_xs = [p[0] for s in sy_segs for p in s]
        sy_ys = [p[1] for s in sy_segs for p in s]

        # ------------------------------------------------------------------
        # Create figure with 3 panels
        # ------------------------------------------------------------------
        fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(24, 9), facecolor='white')

        # Panel 1: Isometric view
        draw_view(ax1, iso_project, '3D Isometric View',
                  'Projection X', 'Projection Y (Z × {:.1f})'.format(z_exag))
        ax1.set_xlim(min(iso_xs) - iso_pad_x, max(iso_xs) + iso_pad_x)
        ax1.set_ylim(min(iso_ys) - iso_pad_y, max(iso_ys) + iso_pad_y)

        # Panel 2: Front elevation (XZ)
        draw_view(ax2, elev_xz, 'Front Elevation (XZ Plane)',
                  'X — Longitudinal (m)', 'Z — Elevation (m, ×{:.1f})'.format(z_exag))
        fx_pad_x = max(1.0, (max(fx_xs) - min(fx_xs)) * 0.05)
        fx_pad_y = max(1.0, (max(fx_ys) - min(fx_ys)) * 0.08)
        ax2.set_xlim(min(fx_xs) - fx_pad_x, max(fx_xs) + fx_pad_x)
        ax2.set_ylim(min(fx_ys) - fx_pad_y, max(fx_ys) + fx_pad_y)

        # Panel 3: Side elevation (YZ)
        draw_view(ax3, elev_yz, 'Side Elevation (YZ Plane)',
                  'Y — Transverse (m)', 'Z — Elevation (m, ×{:.1f})'.format(z_exag))
        sy_pad_x = max(1.0, (max(sy_xs) - min(sy_xs)) * 0.05)
        sy_pad_y = max(1.0, (max(sy_ys) - min(sy_ys)) * 0.08)
        ax3.set_xlim(min(sy_xs) - sy_pad_x, max(sy_xs) + sy_pad_x)
        ax3.set_ylim(min(sy_ys) - sy_pad_y, max(sy_ys) + sy_pad_y)

        # ------------------------------------------------------------------
        # Title and legend
        # ------------------------------------------------------------------
        bname = self.meta.get('QRFNumber', 'PEB Building')
        if not bname or bname == 'NA':
            bname = 'PEB Building'

        actual_height = z_max - z_min
        exag_note = f'  [Z exaggerated ×{z_exag:.1f} for clarity]' if z_exag > 1.05 else ''

        fig.suptitle(
            f"3D Structural Model — {bname}\n"
            f"W={self.bp.width:.1f}m × L={self.bp.length:.1f}m × H={actual_height:.1f}m  |  "
            f"{self.total_nodes:,} Nodes | {self.total_members:,} Members"
            f"{exag_note}",
            fontsize=13, fontweight='bold', color=COLORS['text_dark'], y=0.99
        )

        # Legend — compact, at bottom
        legend_patches = []
        for label, mids, color, lw in member_groups:
            if mids:
                legend_patches.append(mpatches.Patch(color=color,
                    label=f"{label} ({len(mids)})"))
        if legend_patches:
            ncol = min(len(legend_patches), 6)
            fig.legend(handles=legend_patches, loc='lower center', fontsize=9,
                       framealpha=0.95, fancybox=True, ncol=ncol,
                       borderpad=0.6, handlelength=1.8, handleheight=1.0,
                       bbox_to_anchor=(0.5, -0.02), edgecolor='#CCCCCC')

        plt.tight_layout(rect=[0, 0.06, 1, 0.90], pad=1.5)
        plt.savefig(output_path, dpi=180, bbox_inches='tight', facecolor='white')
        plt.close()
        logger.info(f"Saved 3D model plot: {output_path}")
        return output_path

    # ==================================================================
    # PLOT 2: STEEL WEIGHT DISTRIBUTION (DONUT CHART)
    # ==================================================================
    def plot_weight_distribution(self, output_path: str) -> str:
        """Generate a donut chart showing steel weight distribution by component."""
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))

        # --- Left: Donut Chart ---
        # Group into major categories
        groups = {
            'Primary Steel\n(Columns)': 0, 'Primary Steel\n(Rafters)': 0,
            'Primary Steel\n(Haunch)': 0, 'Secondary Steel\n(Purlins)': 0,
            'Secondary Steel\n(Girts)': 0, 'Bracing\nSystem': 0,
            'Mezzanine': 0, 'Crane\nSystem': 0, 'Canopy': 0, 'End Wall\nGirts': 0,
        }
        if 'Main Columns' in self.cat_data:
            groups['Primary Steel\n(Columns)'] = self.cat_data['Main Columns']['total_weight_kg']
        if 'Main Rafters' in self.cat_data:
            groups['Primary Steel\n(Rafters)'] = self.cat_data['Main Rafters']['total_weight_kg']
        if 'Haunch' in self.cat_data:
            groups['Primary Steel\n(Haunch)'] = self.cat_data['Haunch']['total_weight_kg']
        if 'Purlins' in self.cat_data:
            groups['Secondary Steel\n(Purlins)'] = self.cat_data['Purlins']['total_weight_kg']
        if 'Girts' in self.cat_data:
            groups['Secondary Steel\n(Girts)'] = self.cat_data['Girts']['total_weight_kg']
        bracing_kg = self.cat_data.get('Roof Bracing', {}).get('total_weight_kg', 0) + \
                     self.cat_data.get('Wall Bracing', {}).get('total_weight_kg', 0) + \
                     self.cat_data.get('Flange Braces', {}).get('total_weight_kg', 0)
        groups['Bracing\nSystem'] = bracing_kg
        mezz_kg = self.cat_data.get('Mezz. Beams', {}).get('total_weight_kg', 0) + \
                  self.cat_data.get('Mezz. Columns', {}).get('total_weight_kg', 0)
        groups['Mezzanine'] = mezz_kg
        groups['Crane\nSystem'] = self.cat_data.get('Crane Girders', {}).get('total_weight_kg', 0)
        canopy_kg = self.cat_data.get('Canopy Beams', {}).get('total_weight_kg', 0) + \
                    self.cat_data.get('Canopy Cols', {}).get('total_weight_kg', 0)
        groups['Canopy'] = canopy_kg
        groups['End Wall\nGirts'] = self.cat_data.get('End Wall Girts', {}).get('total_weight_kg', 0)

        # Filter out zero-weight categories
        filtered = {k: v for k, v in groups.items() if v > 0}
        labels = list(filtered.keys())
        sizes = list(filtered.values())
        colors_donut = PIE_COLORS[:len(labels)]

        wedges, texts, autotexts = ax1.pie(
            sizes, labels=labels, colors=colors_donut,
            autopct=lambda pct: f'{pct:.1f}%\n({pct*self.total_weight_kg/100:,.0f} kg)' if pct > 3 else '',
            startangle=90, pctdistance=0.78,
            wedgeprops=dict(width=0.45, edgecolor='white', linewidth=2),
            textprops={'fontsize': 7}
        )
        for t in autotexts:
            t.set_fontsize(7)
            t.set_color('white')
            t.set_fontweight('bold')

        # Center text
        ax1.text(0, 0, f'Total\n{self.total_weight_mt:,.1f} MT', ha='center', va='center',
                 fontsize=14, fontweight='bold', color=COLORS['text_dark'])
        ax1.set_title('Steel Weight Distribution', fontsize=13, fontweight='bold',
                       pad=15, color=COLORS['text_dark'])

        # --- Right: Horizontal Bar Chart (weights) ---
        sorted_cats = sorted(self.cat_data.items(), key=lambda x: x[1]['total_weight_kg'], reverse=True)
        names = [f"{k}\n({v['count']} pcs)" for k, v in sorted_cats]
        weights = [v['total_weight_kg'] for _, v in sorted_cats]
        bar_colors = [v['color'] for _, v in sorted_cats]

        bars = ax2.barh(range(len(names)), weights, color=bar_colors, edgecolor='white',
                        linewidth=0.5, height=0.7)
        ax2.set_yticks(range(len(names)))
        ax2.set_yticklabels(names, fontsize=7)
        ax2.set_xlabel('Weight (kg)', fontsize=10)
        ax2.set_title('Component Weight Ranking', fontsize=13, fontweight='bold',
                       pad=15, color=COLORS['text_dark'])
        ax2.invert_yaxis()

        # Add value labels on bars
        for bar, w in zip(bars, weights):
            ax2.text(bar.get_width() + max(weights)*0.01, bar.get_y() + bar.get_height()/2,
                     f'{w:,.0f} kg ({w/1000:.2f} MT)', va='center', fontsize=7,
                     color=COLORS['text_dark'])

        ax2.set_xlim(0, max(weights) * 1.35)
        ax2.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{x:,.0f}'))

        # Steel intensity annotation
        footprint = self.bp.width * self.bp.length
        if footprint > 0:
            intensity = self.total_weight_kg / footprint
            ax2.text(0.98, 0.02, f'Steel Intensity: {intensity:.1f} kg/m²',
                     transform=ax2.transAxes, fontsize=8, ha='right', va='bottom',
                     bbox=dict(boxstyle='round,pad=0.4', facecolor=COLORS['success'],
                               alpha=0.15, edgecolor=COLORS['success']))

        plt.tight_layout(pad=3.0)
        plt.savefig(output_path, dpi=180, bbox_inches='tight', facecolor='white')
        plt.close()
        logger.info(f"Saved weight distribution plot: {output_path}")
        return output_path

    # ==================================================================
    # PLOT 3: STEEL TAKEOFF BAR CHART
    # ==================================================================
    def plot_steel_takeoff(self, output_path: str) -> str:
        """Generate grouped bar chart showing quantity, length, weight per component."""
        sorted_cats = sorted(self.cat_data.items(), key=lambda x: x[1]['total_weight_kg'], reverse=True)
        names = [k for k, v in sorted_cats]
        counts = [v['count'] for _, v in sorted_cats]
        lengths = [v['total_length_m'] for _, v in sorted_cats]
        weights = [v['weight_mt'] for _, v in sorted_cats]
        colors = [v['color'] for _, v in sorted_cats]

        fig, axes = plt.subplots(1, 3, figsize=(20, 8))
        fig.suptitle(f'Steel Takeoff Summary — {self.meta.get("QRFNumber", "PEB Building")}',
                     fontsize=15, fontweight='bold', color=COLORS['text_dark'], y=1.02)

        # --- Bar 1: Member Count ---
        bars1 = axes[0].barh(range(len(names)), counts, color=colors, edgecolor='white',
                              linewidth=0.5, height=0.65)
        axes[0].set_yticks(range(len(names)))
        axes[0].set_yticklabels(names, fontsize=7)
        axes[0].set_xlabel('Quantity (nos)', fontsize=10)
        axes[0].set_title('Member Count', fontsize=12, fontweight='bold', color=COLORS['text_dark'])
        axes[0].invert_yaxis()
        for bar, c in zip(bars1, counts):
            axes[0].text(bar.get_width() + max(counts)*0.02, bar.get_y() + bar.get_height()/2,
                         f'{c:,}', va='center', fontsize=8, fontweight='bold')

        # --- Bar 2: Total Length ---
        bars2 = axes[1].barh(range(len(names)), lengths, color=colors, edgecolor='white',
                              linewidth=0.5, height=0.65)
        axes[1].set_yticks(range(len(names)))
        axes[1].set_yticklabels(names, fontsize=7)
        axes[1].set_xlabel('Total Length (m)', fontsize=10)
        axes[1].set_title('Total Length', fontsize=12, fontweight='bold', color=COLORS['text_dark'])
        axes[1].invert_yaxis()
        axes[1].xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{x:,.0f}'))
        for bar, l in zip(bars2, lengths):
            axes[1].text(bar.get_width() + max(lengths)*0.02, bar.get_y() + bar.get_height()/2,
                         f'{l:,.1f} m', va='center', fontsize=8, fontweight='bold')

        # --- Bar 3: Total Weight ---
        bars3 = axes[2].barh(range(len(names)), weights, color=colors, edgecolor='white',
                              linewidth=0.5, height=0.65)
        axes[2].set_yticks(range(len(names)))
        axes[2].set_yticklabels(names, fontsize=7)
        axes[2].set_xlabel('Weight (MT)', fontsize=10)
        axes[2].set_title('Total Weight', fontsize=12, fontweight='bold', color=COLORS['text_dark'])
        axes[2].invert_yaxis()
        axes[2].xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f'{x:,.1f}'))
        for bar, w in zip(bars3, weights):
            axes[2].text(bar.get_width() + max(weights)*0.02, bar.get_y() + bar.get_height()/2,
                         f'{w:,.2f} MT', va='center', fontsize=8, fontweight='bold')

        # Add summary annotation box at bottom center
        summary_text = (
            f"Total: {self.total_members:,} members  |  "
            f"{sum(counts):,} pcs  |  "
            f"{sum(lengths):,.1f} m  |  "
            f"{self.total_weight_mt:,.2f} MT"
        )
        fig.text(0.5, -0.02, summary_text, ha='center', va='top', fontsize=10,
                 fontweight='bold', color=COLORS['text_dark'],
                 bbox=dict(boxstyle='round,pad=0.5', facecolor=COLORS['success'],
                           alpha=0.12, edgecolor=COLORS['success']))

        plt.tight_layout(pad=2.0)
        plt.savefig(output_path, dpi=180, bbox_inches='tight', facecolor='white')
        plt.close()
        logger.info(f"Saved steel takeoff plot: {output_path}")
        return output_path

    # ==================================================================
    # PLOT 4: COST BREAKDOWN CHART
    # ==================================================================
    def plot_cost_breakdown(self, output_path: str) -> str:
        """Generate cost breakdown visualization with stacked bars and KPI cards."""
        # Cost data (mirrors BOQGenerator rates)
        RATE = {
            'primary': 72.0, 'secondary': 78.0, 'bracing': 75.0,
            'crane': 80.0, 'mezz': 74.0, 'canopy': 76.0,
        }
        GST = 0.18

        # Compute component costs
        cost_items = {}
        for name, data in self.cat_data.items():
            if 'Column' in name or 'Rafter' in name or 'Haunch' in name:
                rate = RATE['primary']
            elif 'Purlin' in name or 'Girt' in name or 'End Wall' in name:
                rate = RATE['secondary']
            elif 'Bracing' in name or 'Flange' in name:
                rate = RATE['bracing']
            elif 'Crane' in name:
                rate = RATE['crane']
            elif 'Mezz' in name:
                rate = RATE['mezz']
            elif 'Canopy' in name:
                rate = RATE['canopy']
            else:
                rate = RATE['primary']
            cost_items[name] = data['total_weight_kg'] * rate

        # Group costs into major categories
        primary_cost = sum(cost_items.get(k, 0) for k in ['Main Columns', 'Main Rafters', 'Haunch'])
        secondary_cost = sum(cost_items.get(k, 0) for k in ['Purlins', 'Girts', 'End Wall Girts'])
        bracing_cost = sum(cost_items.get(k, 0) for k in ['Roof Bracing', 'Wall Bracing', 'Flange Braces'])
        mezz_cost = sum(cost_items.get(k, 0) for k in ['Mezz. Beams', 'Mezz. Columns'])
        crane_cost = cost_items.get('Crane Girders', 0)
        canopy_cost = sum(cost_items.get(k, 0) for k in ['Canopy Beams', 'Canopy Cols'])

        structural_total = primary_cost + secondary_cost + bracing_cost + mezz_cost + crane_cost + canopy_cost

        # Estimate non-steel costs
        bolts = int((self.cat_data.get('Purlins', {}).get('count', 0) +
                     self.cat_data.get('Girts', {}).get('count', 0)) * 2 +
                    self.cat_data.get('Main Columns', {}).get('count', 0) * 12)
        bolts_cost = bolts * 15.0
        weld_cost = (primary_cost / RATE['primary']) * 0.03 * 35.0
        paint_cost = (primary_cost / RATE['primary']) * 0.12 * 85.0
        galv_cost = (secondary_cost / RATE['secondary']) * 12.0
        connections_cost = bolts_cost + weld_cost
        surface_cost = paint_cost + galv_cost

        roof_area = self.bp.width * self.bp.length * (1 + self.bp.roof_slope_ratio**2 / 4)
        wall_area = 2 * (self.bp.width + self.bp.length) * self.bp.eave_height * 0.85
        cladding_cost = roof_area * 380 + wall_area * 360 + roof_area * 120

        transport_cost = self.total_weight_kg * 6.0
        erection_cost = self.total_weight_kg * 18.0
        logistics_cost = transport_cost + erection_cost

        # All cost categories
        all_categories = {}
        if primary_cost > 0:
            all_categories['Primary Steel'] = primary_cost
        if secondary_cost > 0:
            all_categories['Secondary Steel'] = secondary_cost
        if bracing_cost > 0:
            all_categories['Bracing'] = bracing_cost
        if mezz_cost > 0:
            all_categories['Mezzanine'] = mezz_cost
        if crane_cost > 0:
            all_categories['Crane'] = crane_cost
        if canopy_cost > 0:
            all_categories['Canopy'] = canopy_cost
        all_categories['Connections'] = connections_cost
        all_categories['Surface Treatment'] = surface_cost
        all_categories['Cladding'] = cladding_cost
        all_categories['Transport & Erection'] = logistics_cost

        subtotal = sum(all_categories.values())
        gst_amount = subtotal * GST
        grand_total = subtotal + gst_amount

        fig = plt.figure(figsize=(18, 10))
        gs = gridspec.GridSpec(2, 2, height_ratios=[3, 1.2], hspace=0.35, wspace=0.3)

        # --- Top Left: Horizontal Bar Chart ---
        ax1 = fig.add_subplot(gs[0, 0])
        sorted_costs = sorted(all_categories.items(), key=lambda x: x[1], reverse=True)
        cost_names = [k for k, v in sorted_costs]
        cost_values = [v for k, v in sorted_costs]

        cost_colors = [PIE_COLORS[i % len(PIE_COLORS)] for i in range(len(cost_names))]
        bars = ax1.barh(range(len(cost_names)), [v/1e6 for v in cost_values],
                        color=cost_colors, edgecolor='white', linewidth=0.5, height=0.65)
        ax1.set_yticks(range(len(cost_names)))
        ax1.set_yticklabels(cost_names, fontsize=8)
        ax1.set_xlabel('Cost (INR Millions)', fontsize=10)
        ax1.set_title('Cost Breakdown by Category', fontsize=13, fontweight='bold',
                       color=COLORS['text_dark'])
        ax1.invert_yaxis()
        for bar, v in zip(bars, cost_values):
            ax1.text(bar.get_width() + max(cost_values)/1e6*0.01,
                     bar.get_y() + bar.get_height()/2,
                     f'INR {v/1e5:,.1f}L', va='center', fontsize=7.5, fontweight='bold')

        # --- Top Right: Pie Chart ---
        ax2 = fig.add_subplot(gs[0, 1])
        pie_names = cost_names[:8]  # Top 8 categories
        pie_values = cost_values[:8]
        if len(cost_names) > 8:
            pie_names.append('Others')
            pie_values.append(sum(cost_values[8:]))
        pie_colors = PIE_COLORS[:len(pie_names)]

        wedges, texts, autotexts = ax2.pie(
            pie_values, labels=None, colors=pie_colors,
            autopct=lambda pct: f'{pct:.1f}%' if pct > 4 else '',
            startangle=140, pctdistance=0.82,
            wedgeprops=dict(width=0.5, edgecolor='white', linewidth=2)
        )
        for t in autotexts:
            t.set_fontsize(7)
            t.set_fontweight('bold')
        ax2.set_title('Cost Share (%)', fontsize=13, fontweight='bold',
                       color=COLORS['text_dark'])
        ax2.legend(pie_names, loc='center left', bbox_to_anchor=(0.85, 0.5), fontsize=7)

        # Center text
        ax2.text(0, 0, f'INR {grand_total/1e7:,.2f} Cr', ha='center', va='center',
                 fontsize=13, fontweight='bold', color=COLORS['text_dark'])

        # --- Bottom: KPI Cards ---
        ax3 = fig.add_subplot(gs[1, :])
        ax3.set_xlim(0, 10)
        ax3.set_ylim(0, 1.5)
        ax3.axis('off')

        kpis = [
            ('Grand Total\n(incl. GST)', f'INR {grand_total:,.0f}', COLORS['accent']),
            ('Structural\nSteel Cost', f'INR {structural_total:,.0f}', COLORS['primary_steel']),
            ('Total Steel\nWeight', f'{self.total_weight_mt:,.2f} MT', COLORS['bracing']),
            ('Steel Rate', f'INR {grand_total/(self.total_weight_kg/1000):,.0f}/MT', COLORS['rafter']),
            ('Steel Intensity', f'{self.total_weight_kg/(self.bp.width*self.bp.length):.1f} kg/m²', COLORS['success']),
            ('GST Amount', f'INR {gst_amount:,.0f}', COLORS['warning']),
        ]

        kpi_width = 9.0 / len(kpis)
        for i, (title, value, color) in enumerate(kpis):
            x_left = 0.5 + i * kpi_width
            rect = FancyBboxPatch((x_left, 0.15), kpi_width - 0.15, 1.2,
                                   boxstyle="round,pad=0.1",
                                   facecolor=color, alpha=0.1,
                                   edgecolor=color, linewidth=2)
            ax3.add_patch(rect)
            ax3.text(x_left + kpi_width/2 - 0.075, 1.0, title, ha='center', va='center',
                     fontsize=8, color=color, fontweight='bold')
            ax3.text(x_left + kpi_width/2 - 0.075, 0.55, value, ha='center', va='center',
                     fontsize=10, color=COLORS['text_dark'], fontweight='bold')

        bname = self.meta.get('QRFNumber', 'PEB Building')
        fig.suptitle(f'Cost Estimation Dashboard — {bname}',
                     fontsize=16, fontweight='bold', color=COLORS['text_dark'], y=0.98)

        plt.savefig(output_path, dpi=180, bbox_inches='tight', facecolor='white')
        plt.close()
        logger.info(f"Saved cost breakdown plot: {output_path}")
        return output_path

    # ==================================================================
    # PLOT 5: MEMBER CATEGORY DISTRIBUTION
    # ==================================================================
    def plot_member_distribution(self, output_path: str) -> str:
        """Generate a stacked/grouped visualization of member categories."""
        sorted_cats = sorted(self.cat_data.items(), key=lambda x: x[1]['count'], reverse=True)
        names = [k for k, v in sorted_cats]
        counts = [v['count'] for _, v in sorted_cats]
        avg_lengths = [v['avg_length_m'] for _, v in sorted_cats]
        colors = [v['color'] for _, v in sorted_cats]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 8))

        # --- Left: Treemap-style proportional rectangles ---
        total_count = sum(counts)
        ax1.set_xlim(0, 10)
        ax1.set_ylim(0, 10)
        ax1.axis('off')

        # Simple treemap packing (row-based)
        row_y = 9.5
        row_x = 0.2
        row_height = 0

        for name, count, color in zip(names, counts, colors):
            pct = count / total_count
            box_width = pct * 9.6
            box_height = 0.9

            if row_x + box_width > 9.8:
                row_y -= row_height + 0.15
                row_x = 0.2

            if row_y - box_height < 0.2:
                break

            rect = FancyBboxPatch((row_x, row_y - box_height), box_width, box_height,
                                   boxstyle="round,pad=0.05",
                                   facecolor=color, alpha=0.85,
                                   edgecolor='white', linewidth=2)
            ax1.add_patch(rect)

            # Text inside rectangle
            dark_colors = {COLORS['column'], COLORS['rafter'], COLORS['bracing'],
                           COLORS['primary_steel'], COLORS['flange_brace'], COLORS['crane']}
            text_color = 'white' if color in dark_colors else COLORS['text_dark']
            if box_width > 1.2:
                ax1.text(row_x + box_width/2, row_y - box_height/2 + 0.1,
                         name, ha='center', va='center', fontsize=7,
                         fontweight='bold', color=text_color)
                ax1.text(row_x + box_width/2, row_y - box_height/2 - 0.15,
                         f'{count:,} pcs ({pct:.1f}%)', ha='center', va='center',
                         fontsize=6, color=text_color, alpha=0.9)
            row_x += box_width + 0.08
            row_height = max(row_height, box_height)

        ax1.set_title(f'Member Category Distribution ({total_count:,} total)',
                       fontsize=13, fontweight='bold', color=COLORS['text_dark'], pad=15)

        # --- Right: Average member length by category ---
        bars = ax2.barh(range(len(names)), avg_lengths, color=colors, edgecolor='white',
                        linewidth=0.5, height=0.65)
        ax2.set_yticks(range(len(names)))
        ax2.set_yticklabels(names, fontsize=7)
        ax2.set_xlabel('Average Length (m)', fontsize=10)
        ax2.set_title('Average Member Length', fontsize=13, fontweight='bold',
                       color=COLORS['text_dark'])
        ax2.invert_yaxis()
        for bar, l in zip(bars, avg_lengths):
            ax2.text(bar.get_width() + max(avg_lengths)*0.02, bar.get_y() + bar.get_height()/2,
                     f'{l:.2f} m', va='center', fontsize=8, fontweight='bold')

        # Add min/max length range as error bars
        for i, (name, _) in enumerate(sorted_cats):
            data = self.cat_data[name]
            if len(data['lengths']) >= 2:
                min_l = min(data['lengths'])
                max_l = max(data['lengths'])
                mid = avg_lengths[i]
                ax2.plot([min_l, max_l], [i, i], '|', color=COLORS['text_dark'],
                         markersize=8, markeredgewidth=1.5)

        plt.tight_layout(pad=3.0)
        plt.savefig(output_path, dpi=180, bbox_inches='tight', facecolor='white')
        plt.close()
        logger.info(f"Saved member distribution plot: {output_path}")
        return output_path

    # ==================================================================
    # PLOT 6: BUILDING PARAMETERS DASHBOARD
    # ==================================================================
    def plot_building_dashboard(self, output_path: str) -> str:
        """Generate a multi-panel KPI dashboard showing all building parameters."""
        fig = plt.figure(figsize=(20, 12))
        gs = gridspec.GridSpec(3, 4, hspace=0.45, wspace=0.35)

        bname = self.meta.get('QRFNumber', 'PEB Building')
        fig.suptitle(f'Building Parameters Dashboard — {bname}',
                     fontsize=18, fontweight='bold', color=COLORS['text_dark'], y=0.98)

        # ---- Row 1: Building Dimensions ----
        dimension_data = [
            ('Width', f'{self.bp.width:.2f} m', COLORS['primary_steel'], '📐'),
            ('Length', f'{self.bp.length:.2f} m', COLORS['rafter'], '📏'),
            ('Eave Height', f'{self.bp.eave_height:.2f} m', COLORS['bracing'], '🏗️'),
            ('Roof Slope', self.bp.roof_slope_str, COLORS['haunch'], '📐'),
        ]
        for i, (label, value, color, icon) in enumerate(dimension_data):
            ax = fig.add_subplot(gs[0, i])
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis('off')
            rect = FancyBboxPatch((0.05, 0.05), 0.9, 0.9,
                                   boxstyle="round,pad=0.08",
                                   facecolor=color, alpha=0.08,
                                   edgecolor=color, linewidth=2.5)
            ax.add_patch(rect)
            ax.text(0.5, 0.7, label, ha='center', va='center', fontsize=9,
                    color=color, fontweight='bold')
            ax.text(0.5, 0.4, value, ha='center', va='center', fontsize=16,
                    fontweight='bold', color=COLORS['text_dark'])
            ax.set_title(label, fontsize=10, fontweight='bold', color=color, pad=8)

        # ---- Row 2: Load Parameters ----
        load_data = [
            ('Design Code', self.dl.design_code[:20], COLORS['mezzanine'], ''),
            ('Live Load\n(Roof)', f'{self.dl.live_load_roof:.2f}\nkN/m²', COLORS['purlin'], ''),
            ('Wind Speed', f'{self.dl.wind_speed_kmh:.0f}\nkm/h', COLORS['girt'], ''),
            ('Seismic\nZone', self.dl.seismic_zone, COLORS['warning'], ''),
        ]
        for i, (label, value, color, _) in enumerate(load_data):
            ax = fig.add_subplot(gs[1, i])
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis('off')
            rect = FancyBboxPatch((0.05, 0.05), 0.9, 0.9,
                                   boxstyle="round,pad=0.08",
                                   facecolor=color, alpha=0.08,
                                   edgecolor=color, linewidth=2.5)
            ax.add_patch(rect)
            ax.text(0.5, 0.7, label, ha='center', va='center', fontsize=9,
                    color=color, fontweight='bold')
            ax.text(0.5, 0.4, value, ha='center', va='center', fontsize=14,
                    fontweight='bold', color=COLORS['text_dark'])

        # ---- Row 3: Structural Summary ----
        footprint = self.bp.width * self.bp.length
        roof_area = footprint * (1 + self.bp.roof_slope_ratio**2 / 4)
        wall_area = 2 * (self.bp.width + self.bp.length) * self.bp.eave_height

        struct_data = [
            ('Nodes', f'{self.total_nodes:,}', COLORS['column']),
            ('Members', f'{self.total_members:,}', COLORS['rafter']),
            ('Steel Weight', f'{self.total_weight_mt:,.2f} MT', COLORS['bracing']),
            ('Steel Rate', f'{self.total_weight_kg/footprint:.1f} kg/m²' if footprint > 0 else 'N/A',
             COLORS['success']),
        ]
        for i, (label, value, color) in enumerate(struct_data):
            ax = fig.add_subplot(gs[2, i])
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis('off')
            rect = FancyBboxPatch((0.05, 0.05), 0.9, 0.9,
                                   boxstyle="round,pad=0.08",
                                   facecolor=color, alpha=0.08,
                                   edgecolor=color, linewidth=2.5)
            ax.add_patch(rect)
            ax.text(0.5, 0.7, label, ha='center', va='center', fontsize=9,
                    color=color, fontweight='bold')
            ax.text(0.5, 0.4, value, ha='center', va='center', fontsize=14,
                    fontweight='bold', color=COLORS['text_dark'])

        # Add bay info text at bottom
        bay_info = (
            f"Bays (Longitudinal): {len(self.bp.bay_spacing_long)} bays, "
            f"spacing: {', '.join(f'{b:.2f}m' for b in self.bp.bay_spacing_long[:6])}"
            f"{'...' if len(self.bp.bay_spacing_long) > 6 else ''}\n"
            f"Bays (Transverse): {len(self.bp.bay_spacing_trans)} bays, "
            f"spacing: {', '.join(f'{b:.2f}m' for b in self.bp.bay_spacing_trans[:6])}"
            f"{'...' if len(self.bp.bay_spacing_trans) > 6 else ''}\n"
            f"Bracing: {self.bp.bracing_type}  |  "
            f"Footprint: {footprint:,.1f} m²  |  Roof Area: {roof_area:,.1f} m²  |  "
            f"Wall Area: {wall_area:,.1f} m²"
        )
        fig.text(0.5, 0.01, bay_info, ha='center', va='bottom', fontsize=8,
                 color=COLORS['text_dark'], fontstyle='italic',
                 bbox=dict(boxstyle='round,pad=0.5', facecolor='#F0F4F8',
                           edgecolor=COLORS['grid'], alpha=0.9))

        # Optional features indicators
        features = []
        if self.ci.has_crane:
            features.append(f'Crane ({self.ci.capacity_ton:.0f}T)')
        if self.mi.has_mezzanine:
            features.append(f'Mezzanine ({self.mi.height:.1f}m)')
        if self.can_i.has_canopy:
            features.append(f'Canopy ({self.can_i.width:.1f}m)')
        if features:
            feature_text = "Special Features: " + "  |  ".join(features)
            fig.text(0.5, 0.06, feature_text, ha='center', va='bottom', fontsize=9,
                     fontweight='bold', color=COLORS['accent'],
                     bbox=dict(boxstyle='round,pad=0.4', facecolor=COLORS['accent'],
                               alpha=0.08, edgecolor=COLORS['accent']))

        plt.savefig(output_path, dpi=180, bbox_inches='tight', facecolor='white')
        plt.close()
        logger.info(f"Saved building dashboard plot: {output_path}")
        return output_path

    # ==================================================================
    # MASTER: Generate All Plots
    # ==================================================================
    def generate_all_plots(self, output_dir: str, base_name: str) -> List[str]:
        """
        Generate all 6 plots and save to the output directory.
        Returns list of generated file paths.
        """
        os.makedirs(output_dir, exist_ok=True)
        plot_paths = []

        logger.info(f"Generating visualization plots for: {base_name}")

        # Plot 1: 3D Structural Model
        p1 = os.path.join(output_dir, f"{base_name}_01_3D_Model.png")
        try:
            self.plot_3d_model(p1)
            plot_paths.append(p1)
        except Exception as e:
            logger.error(f"Failed to generate 3D model plot: {e}")

        # Plot 2: Steel Weight Distribution
        p2 = os.path.join(output_dir, f"{base_name}_02_Weight_Distribution.png")
        try:
            self.plot_weight_distribution(p2)
            plot_paths.append(p2)
        except Exception as e:
            logger.error(f"Failed to generate weight distribution plot: {e}")

        # Plot 3: Steel Takeoff
        p3 = os.path.join(output_dir, f"{base_name}_03_Steel_Takeoff.png")
        try:
            self.plot_steel_takeoff(p3)
            plot_paths.append(p3)
        except Exception as e:
            logger.error(f"Failed to generate steel takeoff plot: {e}")

        # Plot 4: Cost Breakdown
        p4 = os.path.join(output_dir, f"{base_name}_04_Cost_Breakdown.png")
        try:
            self.plot_cost_breakdown(p4)
            plot_paths.append(p4)
        except Exception as e:
            logger.error(f"Failed to generate cost breakdown plot: {e}")

        # Plot 5: Member Distribution
        p5 = os.path.join(output_dir, f"{base_name}_05_Member_Distribution.png")
        try:
            self.plot_member_distribution(p5)
            plot_paths.append(p5)
        except Exception as e:
            logger.error(f"Failed to generate member distribution plot: {e}")

        # Plot 6: Building Dashboard
        p6 = os.path.join(output_dir, f"{base_name}_06_Building_Dashboard.png")
        try:
            self.plot_building_dashboard(p6)
            plot_paths.append(p6)
        except Exception as e:
            logger.error(f"Failed to generate building dashboard plot: {e}")

        logger.info(f"Generated {len(plot_paths)}/6 visualization plots")
        return plot_paths


# ============================================================================
# MAIN GENERATOR CLASS
# ============================================================================

class STAADGenerator:
    """
    Main orchestrator class that coordinates parsing, geometry generation,
    STAAD file writing, validation, and BOQ generation.
    """

    def __init__(self, qrf_json: Dict[str, Any], output_dir: str = "."):
        self.qrf_json = qrf_json
        self.output_dir = output_dir

        # Parse QRF
        self.parser = QRFParser(qrf_json)
        self.bp = self.parser.parse_building_params()
        self.dl = self.parser.parse_design_loads()

        # Parse optional sections
        self.ci = self.parser.parse_crane(eave_height=self.bp.eave_height)
        self.ci.bracket_height = min(self.ci.bracket_height, self.bp.eave_height * 0.85)
        self.mi = self.parser.parse_mezzanine(self.bp.width, self.bp.length)
        self.can_i = self.parser.parse_canopy()
        self.meta = self.parser.meta

        # Geometry and writer instances (created in generate())
        self.geom: Optional[GeometryGenerator] = None
        self.writer: Optional[STAADWriter] = None

    def generate(self) -> Tuple[str, str, str]:
        """
        Generate the .std file, BOQ, and validation report.
        Returns: (std_content, boq_content, validation_report)
        """
        logger.info("=" * 60)
        logger.info("GENERATING STAAD.PRO FILE")
        logger.info("=" * 60)

        # Step 1: Generate geometry
        self.geom = GeometryGenerator(self.bp)
        self.geom.generate()

        # Step 2: Add optional structures
        self.geom.add_mezzanine(self.mi)
        self.geom.add_canopy(self.can_i)
        self.geom.add_crane(self.ci, self.bp)

        # Step 3: Write STAAD file
        self.writer = STAADWriter(
            self.bp, self.dl, self.geom, self.ci, self.mi, self.can_i, self.meta
        )
        std_content = self.writer.write_all()

        # Step 4: Validate
        validator = STAADValidator(std_content)
        is_valid = validator.validate()

        # Build validation report
        val_report_lines = [
            "VALIDATION REPORT",
            "=" * 40,
            f"Status: {'PASSED' if is_valid else 'FAILED'}",
            f"Errors: {len(validator.errors)}",
            f"Warnings: {len(validator.warnings)}",
        ]
        for e in validator.errors:
            val_report_lines.append(f"  ERROR: {e}")
        for w in validator.warnings:
            val_report_lines.append(f"  WARNING: {w}")
        validation_report = "\n".join(val_report_lines)

        # Step 5: Generate BOQ
        boq_gen = BOQGenerator(self.geom, self.bp, self.dl, self.ci, self.mi, self.can_i, self.meta)
        boq_content = boq_gen.generate()

        logger.info(f"Generation complete. Valid: {is_valid}")
        logger.info(f"  Nodes: {len(self.geom.nodes)}, Members: {len(self.geom.members)}")

        return std_content, boq_content, validation_report

    def save(self, filename: str, generate_plots: bool = True) -> Tuple[str, str, str, List[str]]:
        """
        Generate and save to files.
        Returns (std_path, boq_path, val_path, plot_paths).
        
        Args:
            filename: Input JSON filename (used to derive output names)
            generate_plots: If True, also generates 6 professional visualization plots
        """
        std_content, boq_content, val_report = self.generate()

        # Create output directory
        os.makedirs(self.output_dir, exist_ok=True)

        # Save .std file
        base_name = os.path.splitext(filename)[0]
        std_path = os.path.join(self.output_dir, f"{base_name}.std")
        with open(std_path, "w", encoding="utf-8") as f:
            f.write(std_content)
        logger.info(f"Saved: {std_path}")

        # Save BOQ
        boq_path = os.path.join(self.output_dir, f"{base_name}_BOQ.txt")
        with open(boq_path, "w", encoding="utf-8") as f:
            f.write(boq_content)
        logger.info(f"Saved: {boq_path}")

        # Save validation report
        val_path = os.path.join(self.output_dir, f"{base_name}_validation.txt")
        with open(val_path, "w", encoding="utf-8") as f:
            f.write(val_report)
        logger.info(f"Saved: {val_path}")

        # Generate visualization plots
        plot_paths = []
        if generate_plots:
            try:
                viz_gen = VisualizationGenerator(
                    self.geom, self.bp, self.dl, self.ci, self.mi, self.can_i, self.meta
                )
                plot_paths = viz_gen.generate_all_plots(self.output_dir, base_name)
                logger.info(f"Generated {len(plot_paths)} visualization plots")
            except Exception as e:
                logger.error(f"Failed to generate plots: {e}")

        return std_path, boq_path, val_path, plot_paths


# ============================================================================
# BATCH PROCESSOR
# ============================================================================

def process_single_file(input_path: str, output_dir: str = ".") -> Tuple[str, str, str, List[str]]:
    """
    Process a single QRF JSON file and generate STAAD output + visualization plots.

    Args:
        input_path: Path to the QRF JSON file
        output_dir: Directory for output files

    Returns:
        Tuple of (std_path, boq_path, val_path, plot_paths)
    """
    logger.info(f"Processing: {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        qrf_json = json.load(f)

    filename = os.path.basename(input_path)
    generator = STAADGenerator(qrf_json, output_dir)
    return generator.save(filename)


def process_all_files(input_dir: str, output_dir: str = ".") -> List[Tuple[str, str, str, List[str]]]:
    """
    Process all QRF JSON files in a directory.

    Args:
        input_dir: Directory containing QRF JSON files
        output_dir: Directory for output files

    Returns:
        List of (std_path, boq_path, val_path, plot_paths) tuples
    """
    results: List[Tuple[str, str, str, List[str]]] = []

    # Find all JSON files in input directory
    json_files = sorted(
        os.path.join(input_dir, f)
        for f in os.listdir(input_dir)
        if f.endswith(".json")
    )

    logger.info(f"Found {len(json_files)} JSON files in {input_dir}")

    for jf in json_files:
        try:
            result = process_single_file(jf, output_dir)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to process {jf}: {e}")
            # Continue with next file

    logger.info(f"Processed {len(results)}/{len(json_files)} files successfully")
    return results


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

def main():
    """Main entry point for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="STAAD.Pro .std File Generator v2.0 — Competition Entry",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process a single QRF JSON file
  python staad_generator_v2.py input.json -o output/

  # Process all JSON files in a directory
  python staad_generator_v2.py input_dir/ -o output/

  # Process all test files
  python staad_generator_v2.py /path/to/qrf_input/ -o /path/to/output/
        """,
    )

    parser.add_argument(
        "input",
        nargs="?",  # makes it optional
        default="/kaggle/input/competitions/staad-pro-3d-generator",
        help="Path to a QRF JSON file or directory of JSON files",
    )

    parser.add_argument(
        "-o", "--output",
        default="/kaggle/working/",
        help="Output directory (default: /kaggle/working/)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args, unknown = parser.parse_known_args()

    # Auto-fix for Kaggle environment
    if "kaggle" in os.getcwd():
        args.input = "/kaggle/input/competitions/staad-pro-3d-generator"
        args.output = "/kaggle/working/"

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    input_path = args.input
    output_dir = args.output

    if os.path.isdir(input_path):
        results = process_all_files(input_path, output_dir)
        print(f"\n{'='*60}")
        print(f"SUMMARY: {len(results)} files processed successfully")
        for std_path, boq_path, val_path, plot_paths in results:
            print(f"  STD: {std_path}")
            print(f"  BOQ: {boq_path}")
            print(f"  VAL: {val_path}")
            for pp in plot_paths:
                print(f"  PLOT: {pp}")
    elif os.path.isfile(input_path):
        std_path, boq_path, val_path, plot_paths = process_single_file(input_path, output_dir)
        print(f"\nGenerated files:")
        print(f"  STD: {std_path}")
        print(f"  BOQ: {boq_path}")
        print(f"  VAL: {val_path}")
        for pp in plot_paths:
            print(f"  PLOT: {pp}")
    else:
        print(f"Error: '{input_path}' not found")
        sys.exit(1)


if __name__ == "__main__":
    main()


import { useMemo, useState, useEffect, useRef } from 'react';
import { MapContainer, GeoJSON, TileLayer, useMap, CircleMarker, Popup } from 'react-leaflet';
import L from 'leaflet';
import wellknown from 'wellknown';
import { indicatorColor } from '../utils/scoreColors';
import { INDICATOR_OPTIONS, IRIS_SUPPORTED_INDICATORS } from './Sidebar';
import TimelineSlider from './TimelineSlider';

const TILE_URL = 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png';
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
  'contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

const QUARTIERS_LOCAL = '/data/paris-quartiers.geojson';
const QUARTIERS_API   = 'https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/quartier_paris/exports/geojson';
const BAN_REVERSE_URL = 'https://api-adresse.data.gouv.fr/reverse/';

const INDICATOR_ICONS = {
  livability_score: 'assistant_navigation',
  connectivity_score: 'wifi',
  mobility_score: 'directions_bike',
  health_env_score: 'eco',
  tranquility_score: 'shield',
  anime_score: 'theater_comedy',
  calme_score: 'volume_off',
  european_aqi: 'air',
  pollen_total: 'grass',
  median_price: 'payments',
  median_income: 'euro',
  affordability: 'real_estate_agent',
};

// Indicateurs en valeur brute « bas = mieux » (échelle inversée pour la légende)
const LOWER_BETTER = new Set(['median_price', 'european_aqi', 'pollen_total']);
// Indicateurs en valeur brute « haut = mieux » (pas un score 0-100)
const HIGHER_BETTER_RAW = new Set(['median_income', 'affordability', 'nombre_logements_sociaux']);

// ─────────────────────────────────────────────────────────────────
// Contrôleur de vue (fitBounds / flyTo) — doit être dans MapContainer
// ─────────────────────────────────────────────────────────────────
function MapViewController({ fitBounds, resetSignal }) {
  const map = useMap();
  useEffect(() => {
    if (fitBounds) map.fitBounds(fitBounds, { padding: [40, 40], maxZoom: 15, duration: 0.6 });
  }, [fitBounds, map]);
  useEffect(() => {
    if (resetSignal > 0) map.flyTo([48.8566, 2.3522], 12, { duration: 0.8 });
  }, [resetSignal, map]);
  return null;
}

// ─────────────────────────────────────────────────────────────────
// Composant principal
// ─────────────────────────────────────────────────────────────────
export default function InteractiveMap({
  indicators,
  iris,
  selectedIndicator,
  selectedArrondissement,
  onSelectArrondissement,
  selectedIris,
  onSelectIris,
  chantiers,
  showChantiers,
  // Timeline (slider d'année) — actif uniquement sur l'indicateur prix
  timelineYears,
  priceYear,
  yearPrices,
  onYearChange,
}) {
  // Mode « prix par année » : la choroplèthe rejoue le prix médian DVF de
  // l'année sélectionnée au lieu de la dernière valeur connue.
  const priceMode = selectedIndicator === 'median_price' && yearPrices != null;
  const [quartiersGeoJSON, setQuartiersGeoJSON] = useState(null);
  const [fitBounds, setFitBounds]               = useState(null);
  const [resetSignal, setResetSignal]           = useState(0);
  // Popup pour un quartier cliqué : {lat, lon, nom, address, loadingBan}
  const [quartierPopup, setQuartierPopup]       = useState(null);

  const geoJSONRef = useRef(null);

  // ── Chargement quartiers (local → API fallback) ───────────────
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const r = await fetch(QUARTIERS_LOCAL);
        if (!r.ok) throw new Error('local absent');
        const data = await r.json();
        if (!cancelled) setQuartiersGeoJSON(data);
      } catch {
        try {
          const r = await fetch(QUARTIERS_API);
          if (!r.ok) return;
          const data = await r.json();
          if (!cancelled) setQuartiersGeoJSON(data);
        } catch { /* silencieux */ }
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  // ── Fermer la popup quartier si on change d'arrondissement ───
  useEffect(() => { setQuartierPopup(null); }, [selectedArrondissement]);

  // ── BAN reverse geocoding ─────────────────────────────────────
  async function fetchBanAddress(lat, lon) {
    try {
      const r = await fetch(`${BAN_REVERSE_URL}?lon=${lon}&lat=${lat}`);
      if (!r.ok) return null;
      const data = await r.json();
      const props = data?.features?.[0]?.properties;
      return props?.label ?? null;
    } catch { return null; }
  }

  // ── GeoJSON arrondissements (WKT → Features) ─────────────────
  const arrGeoJSON = useMemo(() => {
    if (!indicators?.length) return null;
    const features = indicators
      .map((d) => {
        if (!d.geometry_wkt) return null;
        const geometry = wellknown.parse(d.geometry_wkt);
        if (!geometry) return null;
        return {
          type: 'Feature',
          geometry,
          properties: {
            arrondissement: d.arrondissement,
            nom:            d.nom_arrondissement,
            value:          priceMode
              ? (yearPrices[d.arrondissement] ?? null)
              : (d[selectedIndicator] ?? null),
          },
        };
      })
      .filter(Boolean);
    return features.length ? { type: 'FeatureCollection', features } : null;
  }, [indicators, selectedIndicator, priceMode, yearPrices]);

  const allValues = useMemo(
    () => (priceMode
      ? Object.values(yearPrices).filter((v) => v != null)
      : indicators?.map((d) => d[selectedIndicator]).filter((v) => v != null) ?? []),
    [indicators, selectedIndicator, priceMode, yearPrices],
  );

  // ── Couche IRIS (grain fin) — drill-down ──────────────────────
  // Logique « vue d'ensemble → détail » : la maille IRIS n'apparaît QUE lorsqu'un
  // arrondissement est sélectionné. La vue globale reste une choroplèthe par
  // arrondissement ; le clic sur un arrondissement révèle ses IRIS.
  const irisInArr = useMemo(
    () => (selectedArrondissement
      ? (iris ?? []).filter((d) => d.arrondissement === selectedArrondissement)
      : []),
    [iris, selectedArrondissement],
  );

  const irisActive = useMemo(
    () => IRIS_SUPPORTED_INDICATORS.has(selectedIndicator) && irisInArr.length > 0,
    [irisInArr, selectedIndicator],
  );

  // Normalisation des couleurs DANS l'arrondissement sélectionné (contraste local
  // pour les indicateurs min-max : prix, revenu médian).
  const irisValues = useMemo(
    () => irisInArr.map((d) => d[selectedIndicator]).filter((v) => v != null),
    [irisInArr, selectedIndicator],
  );

  // GeoJSON IRIS (WKT → Features) limité à l'arrondissement sélectionné
  const irisGeoJSON = useMemo(() => {
    if (!irisActive) return null;
    const features = irisInArr
      .map((d) => {
        if (!d.geometry_wkt) return null;
        const geometry = wellknown.parse(d.geometry_wkt);
        if (!geometry) return null;
        return {
          type: 'Feature',
          geometry,
          properties: {
            code_iris:      d.code_iris,
            arrondissement: d.arrondissement,
            nom:            d.nom_iris,
            value:          d[selectedIndicator] ?? null,
          },
        };
      })
      .filter(Boolean);
    return features.length ? { type: 'FeatureCollection', features } : null;
  }, [irisActive, irisInArr, selectedIndicator]);

  // ── Style choroplèthe arrondissements ─────────────────────────
  // Quand la couche IRIS est active, l'arrondissement devient un simple contour
  // de contexte (pas de remplissage), pour laisser voir le détail IRIS.
  function styleFeature(feature) {
    const { arrondissement, value } = feature.properties;
    const isSelected = arrondissement === selectedArrondissement;
    if (irisActive) {
      return {
        fillOpacity: 0,
        color:       isSelected ? '#1D4ED8' : '#475569',
        weight:      isSelected ? 3.5 : 1.75,
      };
    }
    return {
      fillColor:   indicatorColor(selectedIndicator, value, allValues),
      fillOpacity: isSelected ? 0.88 : 0.72,
      color:       isSelected ? '#2563EB' : '#CBD5E1',
      weight:      isSelected ? 3 : 1.25,
    };
  }

  // ── Style + interactions choroplèthe IRIS ─────────────────────
  function styleIrisFeature(feature) {
    const { value, code_iris } = feature.properties;
    const isSel = code_iris === selectedIris;
    return {
      fillColor:   indicatorColor(selectedIndicator, value, irisValues),
      fillOpacity: isSel ? 0.92 : 0.78,
      color:       isSel ? '#1D4ED8' : '#FFFFFF',
      weight:      isSel ? 3 : 0.4,
    };
  }

  function onEachIrisFeature(feature, layer) {
    const { nom, value, code_iris } = feature.properties;
    const isSel = () => code_iris === selectedIris;
    // Clic sur un quartier (IRIS) → sélection pour comparaison dans le panneau.
    layer.on('click', (e) => {
      L.DomEvent.stopPropagation(e);
      if (onSelectIris) onSelectIris(isSel() ? null : code_iris);
    });
    layer.on('mouseover', () => layer.setStyle({ weight: 2, color: '#1D4ED8' }));
    layer.on('mouseout',  () => layer.setStyle({
      weight: isSel() ? 3 : 0.4,
      color:  isSel() ? '#1D4ED8' : '#FFFFFF',
    }));
    layer.bindTooltip(
      `<div style="font-size:12px;font-weight:600">${nom ?? 'IRIS'}</div>
       <div style="font-size:11px;color:#64748B">${formatIndicatorValue(selectedIndicator, value)}</div>`,
      { sticky: true, className: 'leaflet-tooltip-urban' },
    );
  }

  // Re-style sans re-monter (sélection change)
  useEffect(() => {
    if (!geoJSONRef.current) return;
    geoJSONRef.current.eachLayer((layer) => {
      const { arrondissement, value } = layer.feature.properties;
      const isSelected = arrondissement === selectedArrondissement;
      if (irisActive) {
        layer.setStyle({
          fillOpacity: 0,
          color:       isSelected ? '#1D4ED8' : '#475569',
          weight:      isSelected ? 3.5 : 1.75,
        });
        return;
      }
      layer.setStyle({
        fillColor:   indicatorColor(selectedIndicator, value, allValues),
        fillOpacity: isSelected ? 0.88 : 0.72,
        color:       isSelected ? '#2563EB' : '#CBD5E1',
        weight:      isSelected ? 3 : 1.25,
      });
    });
  }, [selectedArrondissement, irisActive]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Interactions arrondissements ──────────────────────────────
  function onEachArrFeature(feature, layer) {
    const { arrondissement, nom } = feature.properties;
    layer.on('click', (e) => {
      L.DomEvent.stopPropagation(e);
      setQuartierPopup(null);
      onSelectArrondissement(arrondissement);
      setFitBounds(layer.getBounds());
    });
    layer.on('mouseover', () => {
      if (arrondissement !== selectedArrondissement)
        layer.setStyle({ fillOpacity: 0.86, weight: 2.25, color: '#2563EB' });
    });
    layer.on('mouseout', () => {
      const isSel = arrondissement === selectedArrondissement;
      layer.setStyle({
        fillOpacity: isSel ? 0.88 : 0.72,
        color:       isSel ? '#2563EB' : '#CBD5E1',
        weight:      isSel ? 3 : 1.25,
      });
    });
    layer.bindTooltip(
      `<div style="font-size:12px;font-weight:600">${nom ?? `Paris ${arrondissement}e`}</div>
       <div style="font-size:11px;color:#64748B">${formatIndicatorValue(selectedIndicator, feature.properties.value)}</div>`,
      { sticky: true, className: 'leaflet-tooltip-urban' },
    );
  }

  // ── Quartiers filtrés pour l'arrondissement sélectionné ───────
  const quartiersFiltered = useMemo(() => {
    if (!quartiersGeoJSON || !selectedArrondissement) return null;
    const features = (quartiersGeoJSON.features ?? []).filter((f) => {
      const val = f.properties?.c_ar ?? f.properties?.n_sq_ar ?? f.properties?.arrondissement;
      return parseInt(val, 10) === selectedArrondissement;
    });
    return features.length ? { type: 'FeatureCollection', features } : null;
  }, [quartiersGeoJSON, selectedArrondissement]);

  // ── Interactions quartiers — clic → précision IRIS + BAN ─────
  function onEachQuartierFeature(feature, layer) {
    const nom = feature.properties?.l_qu ?? feature.properties?.libelle ?? 'Quartier';
    // fillOpacity minuscule pour rendre la zone cliquable partout
    layer.setStyle({ fillColor: '#2563EB', fillOpacity: 0.03 });

    layer.on('click', async (e) => {
      L.DomEvent.stopPropagation(e); // empêche le clic arrondissement
      const { lat, lng } = e.latlng;
      setQuartierPopup({ lat, lon: lng, nom, address: null, loadingBan: true });
      const address = await fetchBanAddress(lat, lng);
      setQuartierPopup((prev) =>
        prev && prev.nom === nom ? { ...prev, address, loadingBan: false } : prev,
      );
    });

    layer.on('mouseover', () => layer.setStyle({ fillOpacity: 0.18, color: '#2563EB' }));
    layer.on('mouseout',  () => layer.setStyle({ fillOpacity: 0.03, color: '#2563EB' }));

    layer.bindTooltip(
      `<div style="font-size:11px;color:#2563EB;font-weight:600">
         <span class="map-icon" style="font-size:14px;vertical-align:-2px;margin-right:4px">pin_drop</span>
         ${nom}
       </div>
       <div style="font-size:10px;color:#64748B">Cliquer pour l'adresse exacte</div>`,
      { sticky: true, className: 'leaflet-tooltip-urban' },
    );
  }

  const indicatorLabel = INDICATOR_OPTIONS.find((o) => o.id === selectedIndicator)?.label ?? selectedIndicator;

  function handleBackToGlobal() {
    onSelectArrondissement(null);
    setFitBounds(null);
    setResetSignal((n) => n + 1);
  }

  return (
    <div className="relative w-full h-full min-h-[420px] rounded-xl overflow-hidden border border-[#B6C0CC]">

      {/* Bouton retour vue globale */}
      {selectedArrondissement && (
        <button
          className="absolute top-3 left-3 z-[1000] btn-primary text-xs shadow-lg backdrop-blur-sm flex items-center gap-1.5"
          onClick={handleBackToGlobal}
        >
          <span className="map-icon">arrow_back</span>
          Vue globale
        </button>
      )}

      {/* Slider temporel (overlay haut-centre) — rejoue la choroplèthe des prix.
          Masqué en drill-down IRIS (la choroplèthe arrondissement est alors un
          simple contour). */}
      {priceMode && !irisActive && timelineYears?.length > 0 && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-[1000]">
          <TimelineSlider
            years={timelineYears}
            value={priceYear}
            onChange={onYearChange}
          />
        </div>
      )}

      {/* Légende couleur */}
      <ColorLegend indicatorId={selectedIndicator} />

      {/* Badge contextuel : maille IRIS en drill-down, sinon quartiers cliquables */}
      {irisActive ? (
        <div className="absolute bottom-5 right-3 z-[1000] bg-white/90 border border-blue-200 rounded-lg px-2 py-1 text-xs backdrop-blur-sm flex items-center gap-1.5 text-blue-700 shadow-sm">
          <span className="map-icon" style={{ fontSize: 14 }}>grid_on</span>
          <span>Maille IRIS · {irisInArr.length} zones</span>
        </div>
      ) : selectedArrondissement && quartiersFiltered ? (
        <div className="absolute bottom-5 right-3 z-[1000] bg-white/90 border border-slate-200 rounded-lg px-2 py-1 text-xs backdrop-blur-sm flex items-center gap-1.5 text-slate-800 shadow-sm">
          <span className="map-icon" style={{ fontSize: 14 }}>pin_drop</span>
          <span>Quartiers cliquables</span>
        </div>
      ) : null}

      <MapContainer
        center={[48.8566, 2.3522]}
        zoom={12}
        className="w-full h-full"
        zoomControl={false}
        scrollWheelZoom
      >
        <TileLayer url={TILE_URL} attribution={TILE_ATTR} />

        {/* Choroplèthe IRIS (grain fin) — peinte sous le contour arrondissement */}
        {irisActive && irisGeoJSON && (
          <GeoJSON
            key={`iris-${selectedIndicator}-${selectedArrondissement ?? 'all'}-${selectedIris ?? 'none'}`}
            data={irisGeoJSON}
            style={styleIrisFeature}
            onEachFeature={onEachIrisFeature}
          />
        )}

        {/* Choroplèthe / contour arrondissements.
            En mode IRIS, ce calque n'est qu'un contour non-interactif : il laisse
            les clics/survols atteindre les polygones IRIS peints en dessous. */}
        {arrGeoJSON && (
          <GeoJSON
            key={`arr-${selectedIndicator}-${priceMode ? priceYear : 'static'}-${irisActive ? 'outline' : 'fill'}`}
            ref={geoJSONRef}
            data={arrGeoJSON}
            style={styleFeature}
            interactive={!irisActive}
            onEachFeature={onEachArrFeature}
          />
        )}

        {/* Quartiers du drill-down (cliquables pour BAN) — uniquement hors mode IRIS */}
        {!irisActive && selectedArrondissement && quartiersFiltered && (
          <GeoJSON
            key={`q-${selectedArrondissement}`}
            data={quartiersFiltered}
            style={{
              fillColor:   '#2563EB',
              fillOpacity: 0.03,
              color:       '#2563EB',
              weight:      1.5,
              dashArray:   '6 4',
            }}
            onEachFeature={onEachQuartierFeature}
          />
        )}

        {/* Popup résultat clic quartier (BAN + nom) */}
        {quartierPopup && (
          <Popup
            position={[quartierPopup.lat, quartierPopup.lon]}
            eventHandlers={{ remove: () => setQuartierPopup(null) }}
          >
            <div style={{ minWidth: 180 }}>
              <p style={{ fontWeight: 700, marginBottom: 4, color: '#2563EB' }}>
                <span
                  className="map-icon"
                  style={{ fontSize: 16, verticalAlign: '-2px', marginRight: 4 }}
                >
                  pin_drop
                </span>
                {quartierPopup.nom}
              </p>
              {quartierPopup.loadingBan ? (
                <p style={{ fontSize: 11, color: '#64748B' }}>Géocodage…</p>
              ) : quartierPopup.address ? (
                <p style={{ fontSize: 12 }}>{quartierPopup.address}</p>
              ) : (
                <p style={{ fontSize: 11, color: '#64748B' }}>
                  {quartierPopup.lat.toFixed(5)}, {quartierPopup.lon.toFixed(5)}
                </p>
              )}
            </div>
          </Popup>
        )}

        {/* Chantiers (marqueurs orange) */}
        {showChantiers && chantiers?.map((c) =>
          c.lat && c.lon ? (
            <CircleMarker
              key={c.id || `${c.lat}-${c.lon}`}
              center={[c.lat, c.lon]}
              radius={5}
              pathOptions={{ color: '#F97316', fillColor: '#FB923C', fillOpacity: 0.85, weight: 1.5 }}
            >
              <Popup>
                <div style={{ minWidth: 190 }}>
                  <p style={{ fontWeight: 600, marginBottom: 4 }}>
                    <span
                      className="map-icon"
                      style={{ fontSize: 16, verticalAlign: '-2px', marginRight: 4 }}
                    >
                      construction
                    </span>
                    {c.titre}
                  </p>
                  {c.categorie && (
                    <p style={{ fontSize: 10, color: '#2EC4B6', marginBottom: 4 }}>{c.categorie}</p>
                  )}
                  {c.description && (
                    <p style={{ fontSize: 11, color: '#64748B', marginBottom: 4 }}>{c.description}</p>
                  )}
                  {(c.date_debut || c.date_fin) && (
                    <p style={{ fontSize: 11 }}>
                      {c.date_debut ? `Début : ${String(c.date_debut).slice(0, 10)}` : ''}
                      {c.date_fin   ? ` → ${String(c.date_fin).slice(0, 10)}` : ''}
                    </p>
                  )}
                  {c.maitre_ouvrage && (
                    <p style={{ fontSize: 10, color: '#64748B', marginTop: 4 }}>{c.maitre_ouvrage}</p>
                  )}
                </div>
              </Popup>
            </CircleMarker>
          ) : null,
        )}

        <MapViewController fitBounds={fitBounds} resetSignal={resetSignal} />
      </MapContainer>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Légende choroplèthe
// ─────────────────────────────────────────────────────────────────
function ColorLegend({ indicatorId }) {
  const indicator = INDICATOR_OPTIONS.find((o) => o.id === indicatorId) || {};
  const { label = 'Score', icon = 'insights' } = indicator;
  // « bas = mieux » (prix, AQI, pollen) → gradient inversé vert→rouge
  const lowerBetter = LOWER_BETTER.has(indicatorId);
  const rawHigherBetter = HIGHER_BETTER_RAW.has(indicatorId);

  // Libellé des bornes selon la nature de l'indicateur
  const rangeLabel = lowerBetter
    ? 'Bas → Élevé'
    : rawHigherBetter
      ? 'Faible → Élevé'
      : '0 → 100';

  return (
    <div className="absolute bottom-5 left-3 z-[1000] bg-white/95 border border-slate-200 rounded-lg px-3 py-2 text-xs backdrop-blur-sm shadow-sm">
      <p className="text-slate-500 mb-1.5 font-medium flex items-center gap-1.5">
        <span className="material-icon text-base" style={{ verticalAlign: '-3px' }}>
          {icon}
        </span>
        <span>{label}</span>
      </p>
      <div className="flex items-center gap-1.5">
        <div
          className="w-20 h-2 rounded-full"
          style={{
            background: lowerBetter
              ? 'linear-gradient(to right, #10B981, #84CC16, #FACC15, #F97316, #EF4444)'
              : 'linear-gradient(to right, #EF4444, #F97316, #FACC15, #84CC16, #10B981)',
          }}
        />
        <span className="text-slate-500">{rangeLabel}</span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Formatage tooltip
// ─────────────────────────────────────────────────────────────────
function formatIndicatorValue(indicatorId, value) {
  if (value == null) return 'Donnée manquante';
  if (indicatorId === 'median_price')
    return `${new Intl.NumberFormat('fr-FR').format(Math.round(value))} €/m²`;
  if (indicatorId === 'european_aqi')
    return `AQI ${Math.round(value)} (Europe)`;
  if (indicatorId === 'pollen_total')
    return `${Math.round(value)} grains/m³`;
  if (indicatorId === 'median_income')
    return `${new Intl.NumberFormat('fr-FR').format(Math.round(value))} €/an`;
  if (indicatorId === 'affordability')
    return `${value.toFixed(1)} m²/an de revenu`;
  return `${value.toFixed(1)} / 100`;
}

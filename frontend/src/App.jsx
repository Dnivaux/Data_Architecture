import { useState, Component } from 'react';
import DashboardLayout from './components/DashboardLayout';
import { useScores } from './hooks/useScores';
import { useIris } from './hooks/useIris';
import { useLiveMetrics } from './hooks/useLiveMetrics';

export default function App() {
  const [selectedArrondissement, setSelectedArrondissement] = useState(null);
  const [selectedIndicator, setSelectedIndicator] = useState('livability_score');

  const { scores, indicators, scoreMap, indicatorMap, loading, error } = useScores();
  const { iris } = useIris();
  const liveMetrics = useLiveMetrics();

  if (loading) return <LoadingScreen />;
  if (error && !scores.length && !indicators.length) return <ErrorScreen message={error} />;

  return (
    <AppErrorBoundary>
      <DashboardLayout
        selectedArrondissement={selectedArrondissement}
        onSelectArrondissement={setSelectedArrondissement}
        selectedIndicator={selectedIndicator}
        onIndicatorChange={setSelectedIndicator}
        scores={scores}
        indicators={indicators}
        iris={iris}
        scoreMap={scoreMap}
        indicatorMap={indicatorMap}
        liveMetrics={liveMetrics}
      />
    </AppErrorBoundary>
  );
}

class AppErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: null };
  }

  static getDerivedStateFromError(err) {
    return { hasError: true, message: err?.message ?? 'Erreur inconnue' };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen flex flex-col items-center justify-center gap-4 bg-slate-50 text-slate-800">
          <span className="material-icon text-5xl text-rose-500">warning</span>
          <p className="font-semibold text-lg text-slate-800">Erreur d'affichage</p>
          <p className="text-slate-500 text-sm max-w-md text-center">{this.state.message}</p>
          <button
            className="btn-primary mt-2"
            onClick={() => { this.setState({ hasError: false }); window.location.reload(); }}
          >
            Recharger
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function LoadingScreen() {
  return (
    <div className="h-screen flex flex-col items-center justify-center gap-4 bg-slate-50">
      <div className="material-icon text-4xl animate-bounce text-blue-600">location_city</div>
      <p className="text-slate-800 font-medium">Chargement des données Gold…</p>
      <p className="text-slate-500 text-sm">Connexion à PostgreSQL via FastAPI</p>
      <div className="flex gap-1 mt-2">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="w-2 h-2 rounded-full animate-bounce"
            style={{ backgroundColor: '#10B981', animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </div>
    </div>
  );
}

function ErrorScreen({ message }) {
  return (
    <div className="h-screen flex flex-col items-center justify-center gap-4 bg-slate-50 text-slate-800">
      <span className="material-icon text-5xl text-rose-500">warning</span>
      <p className="font-semibold text-lg text-slate-800">Impossible de joindre l'API</p>
      <p className="text-slate-500 text-sm max-w-md text-center leading-relaxed">
        {message}
      </p>
      <p className="text-slate-500 text-xs mt-2">
        Vérifiez que FastAPI tourne sur <code className="text-blue-600">localhost:8000</code>
      </p>
      <button
        className="btn-primary mt-2"
        onClick={() => window.location.reload()}
      >
        Réessayer
      </button>
    </div>
  );
}

import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import NetworkBoundary from './components/NetworkBoundary';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <NetworkBoundary><App /></NetworkBoundary>
  </React.StrictMode>,
);

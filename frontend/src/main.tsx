import {NovaPresentationProvider} from './components/NovaTaskPresentation';
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import UpdateBanner from './components/UpdateBanner';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <NovaPresentationProvider><App /></NovaPresentationProvider>
    <UpdateBanner />
  </React.StrictMode>,
);

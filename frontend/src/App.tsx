import { ChatWindow } from './components/ChatWindow';

function App() {
  return (
    <div
      style={{
        width: '100vw',
        height: '100vh',
        backgroundColor: '#e8ecf1',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <ChatWindow />
    </div>
  );
}

export default App;

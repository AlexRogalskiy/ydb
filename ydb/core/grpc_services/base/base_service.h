#pragma once

#include <library/cpp/actors/core/actorsystem.h>
#include <library/cpp/grpc/server/grpc_request_base.h>
#include <library/cpp/grpc/server/grpc_server.h>
#include <library/cpp/grpc/server/logger.h>

namespace NKikimr {
namespace NGRpcService {

class TGrpcServiceCfg {
public:
    TGrpcServiceCfg(bool rlAllowed)
        : RlAllowed_(rlAllowed)
    { }

    bool IsRlAllowed() const {
        return RlAllowed_;
    }
private:
    const bool RlAllowed_;
};

template <typename T>
class TGrpcServiceBase
    : public NGrpc::TGrpcServiceBase<T>
    , public TGrpcServiceCfg
{
public:
    TGrpcServiceBase(NActors::TActorSystem *system,
                     TIntrusivePtr<::NMonitoring::TDynamicCounters> counters,
                     const NActors::TActorId& proxyId,
                     bool rlAllowed)
        : TGrpcServiceCfg(rlAllowed)
        , ActorSystem_(system)
        , Counters_(counters)
        , GRpcRequestProxyId_(proxyId)
        , GRpcProxies_{proxyId}
    {
    }

    TGrpcServiceBase(NActors::TActorSystem *system,
                     TIntrusivePtr<::NMonitoring::TDynamicCounters> counters,
                     const TVector<NActors::TActorId>& proxies,
                     bool rlAllowed)
        : TGrpcServiceCfg(rlAllowed)
        , ActorSystem_(system)
        , Counters_(counters)
        , GRpcRequestProxyId_(proxies[0])
        , GRpcProxies_(proxies)
    {
        Y_VERIFY(proxies.size());
    }

    void InitService(
        const std::vector<std::unique_ptr<grpc::ServerCompletionQueue>>& cqs,
        NGrpc::TLoggerPtr logger,
        size_t index) override
    {
        CQS.reserve(cqs.size());
        for (auto& cq: cqs) {
            CQS.push_back(cq.get());
        }

        CQ_ = CQS[index % cqs.size()];

        // note that we might call an overloaded InitService(), and not the one from this class
        InitService(CQ_, logger);
    }

    void InitService(grpc::ServerCompletionQueue* cq, NGrpc::TLoggerPtr logger) override {
        CQ_ = cq; // might be self assignment, but it's OK
        SetupIncomingRequests(std::move(logger));
    }

    void SetGlobalLimiterHandle(NGrpc::TGlobalLimiter* limiter) override {
        Limiter_ = limiter;
    }

    bool IncRequest() {
        return Limiter_->Inc();
    }

    void DecRequest() {
        Limiter_->Dec();
        Y_ASSERT(Limiter_->GetCurrentInFlight() >= 0);
    }

protected:
    virtual void SetupIncomingRequests(NGrpc::TLoggerPtr logger) = 0;

    NActors::TActorSystem* ActorSystem_;
    grpc::ServerCompletionQueue* CQ_ = nullptr;

    std::vector<grpc::ServerCompletionQueue*> CQS;

    TIntrusivePtr<::NMonitoring::TDynamicCounters> Counters_;
    const NActors::TActorId GRpcRequestProxyId_;
    const TVector<NActors::TActorId> GRpcProxies_;

    NGrpc::TGlobalLimiter* Limiter_ = nullptr;
};

}
}
